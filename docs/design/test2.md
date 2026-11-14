# Test2: targeted tests for BackOtter (draft)

2026-11-07. A draft for discussion with the user. Of its pieces W1, W2 (landed 2026-11-08), L1, G1 and H1 (landed 2026-11-09), and V1, P1, I1a and I1b with the words GuestTest2, ImgTest2, BkoTest2 and SweepTest2 (landed 2026-11-10), are built; all seven words run. Where the build departs from this draft, its section says so; `testvm/test2/README.md` says how the words run.

**Sources.** Reports in `test2/reports/`, cited as [stic], [holt], [guest], [cost], [yield], [cheap] and [change]. SticTest medians: `test2/data/st1_marks.tsv` (41 checks, 281.9 gm). r3all: `test2/data/ht_rows.json` (40 guests, 186.1 gm) and `test2/data/r3all_files.tsv`. HoltTest history: `test2/data/ht1-runs.tsv`. Classes: `test2/design-classes.md` §2.0. They are copies of the design session's working files as of 2026-11-07. img-one.sh existed only on the hosts (~/backotter-work/images/) until 2026-11-10, when it moved into stick/ with img-lanes.sh and img-xcopy.sh; ht1-queue.sh has been in testvm/holttest1d/ since W1.

**Conventions.** gm is guest-minutes; est. marks an estimate. Test2 costs are estimates built from measured per-guest times. Bytes written ≈ 1.18 × bytes left behind [cost (b)]; 1 GiB = 1.074 GB.

## Summary

- Test2 runs a check only when one of its inputs changed, and only where the change reaches. A ledger keys each result by its inputs; today no result records them [change (d)].
- Seven words: GateTest2 (no VMs), SticTest2, HoltTest2 (with HoltTest1's family letters), GuestTest2, ImgTest2 (rescue images), BkoTest2 (all that is due) and SweepTest2 (everything, fresh, every 14 days).
- The bare word runs what is due, and nothing (in ~10 s) when nothing changed. Guest names or a family letter run those cells fresh; `All` runs the whole scope fresh; `plan` shows what would run and why.
- W1 and W2 come first (~3 days, est.): ht1.sh runs in RAM under reservations and deletes on a pass. HoltTest1All then writes ~103 GB a run, not ~200, or ~0–5 GB if its 6 largest guests take turns in RAM (est.).
- Costs, Test1's in brackets: nothing changed 0 gm (510 gm, 227 GB); a bko-holt fix ~28 gm if the plans show it, ~216 if not (191 gm, ~200 GB); a judge-only change 0 gm (191 gm); a fork change reaching every OS ~320 gm and ~0 GB, slower than SticTest1 but with whole-machine restores (282 gm, ~14 GB).
- "Every Linux guest gets its own pass" stays (decision 1); W1 changes HoltTest1's definition (decision 10). Build: ~14.5 days for phase 1, ~36 in all (est.).

## Why

1. **Reruns on unchanged inputs find nothing.** Since 11-03, every archiver or holt defect came from a new guest, layout or kind of test, or a first run after a change. ~2 h 55 min of SticTest1 and 63 of r3all's 191 slot-minutes reran passed inputs [change (b)].
2. **Duplicates find the same defect at once.** #3 hit 10 guests, #1 and #4 four each; no second guest of one OS, filesystem and version found more. Fixture cases still split classes (#4, #5) [yield (c)].
3. **Cheap layers could have found half the findings first** (#2–4, #8, #10–12, #15, #16, #19 [yield (a)]). Yet holt's 243 tests run only by hand, fs_roundtrip.py last ran 11-01, the fork suites 11-04/05, and judges have no self-tests [cheap (a)].
4. **HoltTest1 is the big writer.** HoltTest1All writes ~200 GB a run and leaves 146 GiB, 70% of it restored disks that ht1.sh never deletes (ht1-bsd.sh:694-696 does). 73% of the P3's writes after its 11-06 boot fell in HoltTest1 windows, shared with rel4 and GuestTest1 run 2. At 0.51 TB/day the P3 lasts ~2.1 years at a recalled 440 TBW, ~4.4 by its wear counter [cost (c)–(e)].
5. **Other work writes more:** Chopin's 11-06 migration ~1.2 TB (556 GB to the P3), the fio test 1.21 TB, compare.py `!` rounds 239 GB for 10 guests; 59% of Cinqcent's scratch writes fell outside every test window [cost (b)–(d)].
6. **Nothing selects by change.** affected.sh picks all 7 platforms for 44 of 58 restic commits, maps holt, guests and rescuers to nothing, and feeds no Test1 driver; no load ever ran from a real rescue image [change (b)–(d)].
7. **GuestTest1's widest read finds nothing.** `--status` hashed 213 GiB per host for no defect; the `--try` log sweep found 61 of 78 and 64 of 64, after ~1.5 h of agents reading [guest (a), (b)].

## The tests

### How every Test2 decides

A **cell** is one SticTest check, HoltTest guest (or guest plus variant), GuestTest guest, gate layer or image. Its **key** hashes:
- **binaries:** each one's sha256 and build-round.sh input digest (per OS after M1), including the rustic-bko HoltTest restores with;
- **the guest:** vm-run-options, ssh-user and passphrase files, and a **fingerprint** (kernel, boot-chain and storage-package versions, layout) from a dump of the image itself, stored with its generation. The dump comes from the post-update boot of the overlay "GuestTest2 update" merges, or a probe boot (`vm-update --probe --write-fingerprint`, ~1 gm; `t2 guest-fingerprints`); never from `--try`, whose overlay is thrown away. As built (V1, 2026-11-10) it is `vms/G/image-fingerprint.json`, with two projections of one record, since HoltTest2 and SticTest2 define theirs differently (HoltTest2's Scope 4, SticTest2's Scope); a guest with no fingerprint at its current generation keys on the generation, which is always safe. A new generation with the same fingerprint makes cells soft-stale;
- **harness:** the commit of the run's private testvm/ copy, and hashes of the check, its helper manifest and the judges;
- **rescuer:** its sha256 or generation, plus the packages ht1.sh installs in it (bin/*.deb, bin/bcachefs; ht1.sh:282);
- **cross-guest inputs:** haiku6 keys on the Linux restic-bko that reads its repository, alpinebsd on netbsd11's generation [stic (a)];
- **hosts**, as a soft key: kernel, OpenZFS, QEMU, OVMF.

A cell is **due** when a scope rule selects it and it has no PASS at its current key. A changed cell that no rule selects is **soft-stale**. A soft-key change (a host update) runs the canaries and makes the other cells soft-stale. Soft-stale cells run only with `stale`, in SweepTest2, or two at a time beside a run with due cells.

**Carried PASSes.** When a rebuild changes a binary but no scope rule selects its platform's cells, they keep their PASS, marked carried. That covers a version-only change (holt's Cargo.toml and Cargo.lock, as in 617c4de and 620ec60; restic's global.go), code the module map and plan diff show can't reach that platform, and DragonFly's rustic-bko rebuilt against an updated dragonfly6. Before an image ships a carried binary, that platform's cheapest cell runs once (est. ≤42 gm for all seven bko-holt platforms); after a version-only change, img-one.sh's `--version` checks suffice.

| The user says | What runs | When nothing changed |
|---|---|---|
| the bare word | its due cells | nothing but GuestTest2's 7-day `--try`s; prints the plan (~10 s) |
| it with guest names or a family letter | those cells, fresh | the same |
| it with `stale` | due and soft-stale cells | the soft-stale cells |
| its `All` form | the whole scope, fresh | the same |
| any form with `plan` | nothing: selection, reasons, est. gm, wall, GB | the same |

**Every run:**
- Preflight: temperatures, RAM and RAM disk, the kvm group, free space, nothing of ours left over.
- A private copy of testvm/ at a named commit (sync-hosts-shared-dirs.md's pattern), since any session's sync changes the shared tree (stictest1/README.md:55-58). The cmp-run.sh queue and ledger stay shared; vm-update merges from the synced main tree, whose commit sync-hosts.sh will record. An uncommitted copy records no PASS until the same diff is committed, so a fix's own test still counts as its recheck (round-speedups.md).
- Scripts drive; agents only diagnose and read new log lines, answering by structured return (no-sendmessage-to-workflow-agents.md).
- RAM first, always under `cmp-run.sh --reserve` from measured peaks. Delete on a pass; a failed run's state moves to /bkoscratch before its diagnostic rerun.
- A FAIL reruns once. A pass then is a FLAKY-FAIL and fails the run (#14 was a race earlier runs passed), unless it matches a known flake, which is reported but fails nothing: ghostbsd26-zfs's ssh exit 255 after boot, or freebsd15-ufs's and ghostbsd26's rustic→restic direction ending without a verdict [yield (c)]. A new LIMIT is a FAIL until classed.
- Stop rules: a failed fast gate starts no guest of its family, and a failed slow gate stops new starts; one need failing on 2 guests of an OS stops that OS; 2 harness-class results (known flakes aside) stop the run; >1 GiB of swap-out or twice the write budget starts nothing new.

**Records.** Append-only JSONL per host, merged on the Mac: key, manifest, selecting rule, per-need verdicts, gm, wall, SSD bytes by wear tag, host, fixture seed. Each report ends with a **wear line** per host and disk. **Budgets** warn for two weeks, then bind: ≤2 GB per host per run (SweepTest2 ≤5 GB), ≤50 GB per host per day.

### GateTest2 (new): no VMs

**Trigger.** "GateTest2" runs the layers whose inputs changed, or prints "nothing due" in seconds. "GateTest2All" runs every layer (est. 35–45 min per host). The other words run their family's fast part first and its slow part beside their guests.

**Fast part (blocks):**
- **holt:** cargo test (243 tests); golden plans for the 45 recorded dumps (bugreport::check_plan, bugreport.rs:465) with invariants (/ mounted, every fstab mount planned, the archiver found, pool disks in use, disk IDs round-tripped); the old-against-new plan diff, which names HoltTest2's guests; redact_dumps.
- **archivers:** the fork suites as root on tmpfs (the 15 root-only restic tests run); symbol versions against FreeBSD 14.5; the Linux rustic-bko built for musl, without glibc NSS (#1 was a static glibc build); version strings.
- **judges:** a planted difference per metadata kind for the 15 judges; a ledger self-test; after a judge-only change, a re-judge of kept records.

**Slow part (beside the guests):**
- fs_roundtrip.py, sparse-rt.sh and nonroot-rt.sh in /mnt/bko-ram, four directions, against expected-coverage.json: today's 12 filesystems plus nilfs2, ext3, btrfs zstd and nocow, bcachefs at 512-byte blocks and ZFS xattr=on, 18 runs. Chopin's 6.12 bcachefs has no casefold, so #9's casefold half stays with nixos2605.
- Added cases: 16 and 64 KiB xattrs (torture.sh:46 stops at 3,000 bytes), a hard link changed between backups, near-full compressed btrfs and bcachefs, and every fixture case that needs no LSM from the 28 checks the Linux binaries run (27 Linux guests and sculpt2610). The 13 non-Linux templates and the SELinux cases need their guests.
- The OS-gated fork tests, in the build guests.

**Pass.** Each due layer passes or gives a LIMIT the baseline allows; a plan diff is accepted once its guests pass HoltTest2. **Cost.** Fast part 2–10 min (fork suites 97 s warm, 8.8 min cold). Slow part ~30–35 min per host at 4–6 runs at once (8 took 26 min [cheap (a)]), plus ~30 gm of build guests; est. ≤2 GB of build cache.

### SticTest2: the archivers

**Trigger.** "SticTest2 [ROUND]" runs the due checks on ROUND's binaries (default: build-round.sh at the fork heads), or nothing when nothing changed. "SticTest2 [ROUND] GUEST…" runs those checks fresh; "SticTest2All" runs all 41 fresh, which is SticTest1's scope.

**Scope.** A cell is due when its OS's binary, its check or helpers, its cross-guest inputs or its fingerprint changed. Here the fingerprint is the kernel line, the OpenZFS or bcachefs-tools minor, the LSM policy major, and the minor of the fixture's mkfs and attr tools; a patch-level move makes the cell soft-stale. Host-side checks (sculpt2610, Artix's host step, haiku6's host read) also key on host versions.
- **Linux:** every due cell runs, as the user ruled on 11-01.
- **Other systems:** the stalest eligible member of each class runs: {freebsd15, freebsd15-geli, ghostbsd26-zfs}, {freebsd15-ufs, hardenedbsd15, ghostbsd26}, {netbsd11, alpinebsd}. Eligible means its recorded cells and need set equal the class's (19 and 21 shared needs in rel4 [stic (c)]); a FAIL fans out to the class. freebsd14, openbsd8, dragonfly6, omnios58 and haiku6 stand alone.
- **Plumbing** (cmp-run.sh, guest-slot.sh, vm-run, vm-lib.sh): one canary per run style (firmware mode, passphrase method, overlay place), the rest canary-verified (decision 4): debian12 (RAM-disk overlay), zorin18 (guest tmpfs), debian13-zfs (ZFS on the RAM disk), freebsd15-ufs (/dev/shm), omnios58 (two boots), haiku6 and sculpt2610 (host side), debian13-stack (UEFI with Secure Boot, vm_boot's console passphrase, vm-lib.sh:77-80) and omarchy (UEFI without, LUKS typed through QEMU's monitor), 54.6 gm. Nine SticTest guests boot UEFI.
- **Judging helpers:** self-tests and a re-judge.

**Steps.** (1) Preflight. (2) build-round.sh, reusing unchanged builds. (3) The fast archiver gate. (4) `plan`. (5) `drive.py --logs-mark MARK ROUND GUEST…`, longest first, canaries first after a harness change, beside the slow gate. (6) Ledger rows and a report: per check, ran or skipped, verdict, why, gm, GB. SticTest1's fix rules stay.

**Pass.** No FAIL or FLAKY-FAIL; every LIMIT already in BACKOTTER.md. **Writes.** aerynos's disks and zfsmirror's spare go to /mnt/bko-ram, zfsmirror alone among its host's large guests (7.67 GiB of overlays [stic (a)] plus est. 9–14 GiB of spare, ~17–22 GiB of Chopin's 28.9 GiB budget). It waits for room rather than fall back to disk, where it writes ~14 GB a run [cost (b)].

**Cost.** FreeBSD only (after M1): freebsd14 11.95 + class means 12.5 + 6.6 = **31 gm**, ~25 min. Every OS: Linux 180.6 (27 checks plus sculpt2610) + 8 other runs (11.95 + 12.52 + 6.58 + 6.03 + 5.67 + 7.37 + 6.17 + 0.86 = 57.1) = **238 gm**, ~45 min at 5–6 guests at once (est.; rel2 ran ~200 gm in 37 min). SticTest2All: 282 gm, 55–65 min. All ~0 GB.

### HoltTest2: whole machines

**Trigger.** "HoltTest2" runs the fleet's due cells, or nothing when nothing changed. "HoltTest2D", 2A, 2M, 2U, 2Z, 2F, 2C, 2V, 2BSD, 2H, 2I and 2Obscure run that family's guests fresh, as HoltTest1's letters did (decision 11); guest names do the same. "HoltTest2All" runs all 45 sources and their variants fresh (49 as built: endeavouros, endeavouros-grub and manjaro joined on 2026-11-09, ubuntu26-luks on 11-10). Variant words add `+bigger`, `+oneshot`, `+degraded`, `+close` (20 mounts and closes) or `+img` (a real rescue image as rescuer: ImgTest2's panel, 12 cells, built with I1, 13 since freebsd15-geli joined on 2026-11-14, geli.md D6; `+close` is still refused until H3).

**Scope.**
1. **Load code:** guests whose golden plan changed, with their variants if partition, assemble or close changed. All four load fixes since bring-up (1626714, 2e6c1b5, 78018db, 3671e96) touched commands.rs, which prints the plan.
2. **Code no plan shows** (exec.rs, status.rs, restore, the boot stage, other/*/load.rs): every guest in that code's row of holt.md (c)'s map; load/{mod,layout,plan,exec,status} reach all 45.
3. **Dump code:** those rows' guests are re-dumped in a probe boot (~1 gm each); only changed dumps or plans run.
4. **Guest updates:** the fingerprint shows a changed kernel, boot-chain (grub, shim, systemd-boot, dracut, initramfs-tools, mkinitcpio, mkinitfs, Limine, kernelstub) or storage package, or a structural change: a difference judge.py would report between the new dump and the last, versions aside (decision 5).
5. **Rescuers:** the oldest target for each thing a rescuer writes. BIOS: debian12 (ext4 onto 6.1), ubuntu24 (OpenZFS 2.2.2), rocky10-raid5 (XFS on md), nixos2605 (bcachefs). UEFI: debian13-stack (md, LUKS, LVM onto 6.12; #13), aerynos (f2fs-tools). The plain cells' BIOS rescuer is a debian13 copy, so that half tests Debian 13's mkfs tools, not the image's; since I1a the same panel's `+img` cells (ImgTest2 step 4) test the image's own. A live BSD rescuer (freebsd15-ufs, netbsd11, haiku6) takes the stalest guest of each system it rescues, plus freebsd14. R1 refreshes the one-time OpenBSD, DragonFly and illumos rescuers when their guests update.
6. **rustic-bko:** one guest per changed platform; on Linux, omarchy (#11) and nixos2605.
7. **New guests and layouts:** every applicable variant plus `+close`. `+close` also runs on the stacked and pool guests after a udev change: a diff to close() in commands.rs, to exec.rs, or to a line running udevadm (commands.rs, plan.rs, other/load.rs).
8. **Judges only:** a re-judge.

**Steps.** (1) build-holt.sh for the changed platforms, the fast holt gate, `plan`. (2) ht1.sh, ht1-bsd.sh or ht1-offline.sh through cmp-run.sh, with all four phases and every refusal (they guard against writing the wrong disk), in RAM under `--reserve D 4` (D: the measured peak; at first r3all's left size plus overlays), the 6 guests that leave over 10 GiB one at a time per host, and the round's shipped rustic-bko. Variants run inside their base run's job. (3) `+close` throttles the targets to disk-like speed (QEMU throttle groups, est.) beside other jobs: debian13-stack passed ~9 runs, then failed twice in a row on r2all's loaded hosts (ht1-runs.tsv). (4) A pass keeps logs, dumps, plans, report and zstd fsmeta or mtree and deletes the rest; a FAIL moves everything to /bkoscratch first. (5) A PASS that can't count because its run made the one-time rescuer copy (dragonfly6's, openbsd8's, omnios58's), which the plan keyed missing, reruns once in the same run at the copy's sum; a pass then is a PASS, not a FLAKY-FAIL.

**Pass.** HoltTest1D's checks (refusals, first and last MiB, health, fsmeta, dump judge, booting alone), the plan invariants, and 20 of 20 closes under `+close`. That only bounds a race below ~15% (est., rule of three); a failure is reported as N of 20.

**Cost.**
- XFS mkfs (7 plans): 1.9 + 3.4 + 5.2 + 4.2 + 3.5 + 2 RAID guests × ~5 = **~28 gm**, ~15 min, ~0 GB.
- Code no plan shows: all 45 sources, 186 + 5 RAID or mirror guests × ~6 = **~216 gm**, ~1.5 h. 0 GB with the big guests taking turns in RAM; ~103 GB if the 6 largest go to disk (81.4 GiB × 1.18).
- Every Linux guest: 119.3 + 3 RAID guests × ~6 = **~137 gm**, ~50 min.
- rustic-bko on every platform: omarchy 9.4 + nixos2605 3.2 + freebsd15 4.1 + netbsd11 4.2 + openbsd8 6.3 + dragonfly6 15.4 + omnios58 5.6 + haiku6 3.4 = **~52 gm**, ~20 min.
- Rescuer panel: 2.65 + 3.8 + ~6 + 3.2 + 3.4 + 6.0 = **~25 gm**.
- HoltTest2All: 186 + 5 × ~6 + ~31 of variants (7 rebuilds × 3.05 + 2 degraded × 4.65) = **~247 gm**, ~1.5–2 h.

### GuestTest2: guest health

**Trigger.** "GuestTest2" checks guests whose generation, builder, update scripts (vm-update and their OS's sections of in-guest.sh), vm-run-options or host QEMU/OVMF changed, plus any guest with no `--try` in 7 days, since upstream repositories are in no key. When nothing changed it runs only those 7-day guests. It is read-only. It covers every guest of `testvm/test2/guest/spec.json` (51 in 2026-11-10's build), leaving out freebsd15-builder (a build helper); haiku6 takes a probe boot, redox and sculpt2610 a console probe, as GuestTest1 kept them out of `--try`. "GuestTest2 GUEST…" runs those guests fresh; "GuestTest2All" is GuestTest1's graded audit with sha256 identity; "GuestTest2 update" is the real update round.

As built, trim.sh, update/pkgset.sh, update/fingerprint.py and holttest1d/judge.py are in no guest's key: `--try` exercises none of them in a way that decides a need, and a change to one would rerun ~50 guests for nothing. An update's row records what they gave (`extra.trim_rc`, `extra.merge`).

**Steps** (as built, 2026-11-10).
1. **Identity:** image-state on every host that holds the guest (a cache host is asked only about the guests its facts list), each disk's size, mtime and inode against its merge record (`vms/G/image-stat.json`, written at each merge), `qemu-img info -U` (qcow2, no backing file, not dirty or corrupt, the `fresh-install` snapshot there); sha256 only on a mismatch, where a match backfills the record. `GuestTest2All` always hashes. A guest that fails gets an `identity` FAIL row, and no job runs for it.
2. **Image checks, in the `--try`'s first boot** (a `before` hook over SSH, before in-guest.sh changes anything), not with vm-mount: package sources (#19), the loader's default entry (#18), zfs-list.cache (#20), free space (a FAIL at 90% of any mount, or under 200 MiB free on /boot or an ESP; a note from 80%), the kernel count (FAIL over each updater's `kernels_max`, from 2 for pacman, Void and Haiku to 5 for dnf). Every due guest boots for its `--try` anyway, so a mount saves nothing; vm-mount reads only Linux roots and picks freebsd14's older boot environment, and dropping it drops the LVM name lock with it. A guest that doesn't boot gets no image checks: the failed boot is the finding. haiku6, which has no `--try`, takes a probe boot (`vm-update --probe`: the hooks, no in-guest.sh, the overlay discarded).
3. **`--try`** in RAM under a reservation (`VM_UPDATE_RAM`, sized from the guest's measured peak, else 8 GiB, at most 16), without trim. `check-after.sh` reads the guest before the update and again after its reboot: boot-state.sh (on the BSDs and illumos, ht1-bsd.sh's service-state rules), failed units, `findmnt --verify`, every fstab mount mounted and not shadowed, the newest kernel running. A line in both readings is **standing**, a LIMIT that stays unclassed until `guest/limits.json` classes it; a line new after the update is a FAIL. `--try` takes no dump: nothing reads one, and a dump that decided a need would make bko-holt an input of every guest cell.
4. **New guests** (`established: false` in the spec, nine on 2026-11-10: endeavouros, endeavouros-grub, manjaro, fedora44-raid, ubuntu26-raid, ubuntu26-luks, rocky10-raid5, freebsd15-mirror, omnios58-mirror): the firmware the guest booted against the one it claims, both archivers' `--version`, a smoke backup of `/etc` and its `check`; redox and sculpt2610 a console probe on every run (`new:probe`). Such checks found 10 of run 1's 78 defects [guest (b)].
5. **Log sweep:** the run's `update.log` and `serial.log`, normalized, less `guest/known-lines.json`'s patterns, diffed against the guest's accepted baseline, which lives on the Mac and never in the repo (it can hold machine IDs and hostnames). Agents read only the new lines (`t2 guest-lines RUN`), and only `--accept` adds to a baseline. A FAIL whose only causes are new lines or unclassed standing lines is **to-class**: reported, exit 1, never rerun and never due until it is classed.
6. **Grades:** GuestTest1's (solid, usable, unusable, not covered, offline-only) plus "unverified at this generation", each by a rule; the backup column from the ledger's stic and holt rows.

**"GuestTest2 update"** is vm-update's real form with the same checks: check-after.sh before the update and after its reboot also decides the merge (`--merge-gate`), since a merged overlay can't be undone. A line new against the pre-update boot, or a hook that failed, discards the overlay, and the row says `merged: false` and why; the way past it is a fix in in-guest.sh or a classed LIMIT, then the update again. It merges only when the package set (`update/pkgset.sh`, before and after) or an in-guest.sh fix step changed (in-guest.sh prints a `step:` line for each it applied), or when either set can't be read: 3e9c8ca and dd0db8e fixed guests with no package change, while arch's u4 merged a "nothing to do" [guest (c)]. Its post-reboot dump becomes the new generation's fingerprint. A merged update isn't the cell's PASS: the merged image hasn't booted, so the guest stays due until a `--try` boots it. One lock serves the fleet (two drivers updated 18 guests twice on 11-06): `.vm-update.fleet.lock` on Chopin, which the Mac holds for the round and each job holds shared for its life, and by-hand updates are refused while it is held. If the Mac's holder goes (a sleep, a dropped ssh), the round's name goes with it: no later job of that round starts, and the report says so. The round also refuses unless the synced tree's vm-update, update scripts, guest checks, t2-host and t2lib are this checkout's, since the merge gate runs from that tree. Wave 1 is the cheapest guest of each package manager, wave 2 the rest of the package managers whose wave-1 guest passed. The overlay stays in RAM and is shipped and committed on every fleet host (Chopin alone since 2026-11-13), one host at a time, this host last (update-guests-not-copy.md). A bare round refuses a guest with no `--try` PASS in 7 days; haiku6 updates only when named.

**Pass.** Identity holds; the checks are clean or each new line is classed. **Cost.** Nothing due: ~10 s. A weekly `--try` of the 48 guests that have one: est. **~104 gm** (1.8 gm a guest, run 2's 1.26 plus ~0.5 for checks; dragonfly6 6, a new guest 3.3), ~19 min, ~0 GB (before V1: 3–8 GB per host plus 213 GiB hashed); `GuestTest2All` est. ~110 gm. The first host runs (2026-11-10) took 0.8–3.1 gm a guest (the probes 0.2–0.7) and wrote 0.00 GB. Agents: est. ≤15 min, not ~1.5 h.

### ImgTest2 (new): rescue images

**Trigger.** "ImgTest2" tests the images img-one.sh built since the last ImgTest2 PASS, and the panels still owed, or nothing if there are none. "ImgTest2 PLAT…" tests those images fresh, and "ImgTest2All" every current image with its panel. A **release** is the user's word that an image will ship (`ImgTest2 PLAT… release`, `ImgTest2All release`); stick/ has no publishing step today. A release requires the panel, and warns, without refusing, when no complete SweepTest2 ran in the last 7 days.

**Steps** (as built, 2026-11-10).
1. Refuse an image unless every binary in it has a PASS at its digest, real or carried with its canary: restic-bko and rustic-bko from SticTest2 or HoltTest2 rows at the digest `images/P/bko-bin.sha256` records, bko-holt by the commit img-one.sh writes beside the image (`images/P/bko-holt.commit`), whose holt code must have a HoltTest2 PASS. A carried PASS counts only through version-only links. The refusal names the binary and the command that would give it a PASS; a bare run finishes its other images, then exits 2. Today's images are all refused: they predate `bko-holt.commit`.
2. **Pin** (`t2-host tool img pin`): the `--test` image is hard-linked (or reflink-copied) into `HOST_ROOT/img-pin/P/` and renamed over the last pin, so a running test keeps its inode; with it its three binaries, checked against `bko-bin.sha256`. A build under way (no `done.exit`, or its lock held) makes the cell not-runnable.
3. **Boot tests**, rerun, never read from img-one.sh's `result.txt`: `stick/boot-test.py` from the run's private copy under `cmp-run.sh --reserve 1 5`, in bios, uefi, uefi-secureboot and cdrom for Linux, bios and uefi elsewhere, a need per check. `stick/P/manifest.sh`, typed in the first mode, reports the kernel, ZFS, the storage tools and the three binaries, and the row keeps it.
4. HoltTest2 `+img` on the oldest-target panel (rule 5's six Linux guests plus freebsd14, freebsd15-geli (since 2026-11-14: the image's own `geli` makes the provider, geli.md D6), netbsd11, openbsd8, dragonfly6, omnios58 and haiku6) before a release, and whenever the image's manifest shows a new kernel, OpenZFS, or mkfs, zpool, mdadm, cryptsetup or LVM tool against the last image whose whole panel passed. This is one-kernel-rescue.md's test: a restore onto an old distro boots there. Other rebuilds (at least 4 of the Linux image from 11-05 to 11-07 [change (b)]) get the boot tests only. The panel runs as one child HoltTest2 run, `t2h-STAMP-img`; a panel that didn't finish is owed, and the next bare ImgTest2 runs it alone (`img:panel-owed`). A panel that can't pass in a build is marked blocked in classes.json with its reason: that image's line says `NOT TESTED`, and `release` fails.

**How `+img` reaches a `--test` image** (I1a, I1b): no image carries a key. img-boot.py picks the console menu's shell on the serial console and types a key made for that run and an `sshd -o ...` line, which win over the image's own settings, so the image is the rescuer as a user gets it, its own bko-holt and rustic-bko doing the load. The key lives only in the run's RAM directory and goes at the rebuild's end. f2fs-tools, nilfs-tools, 9p and the bcachefs module were already in the Linux image; the Linux run asserts every tool and installs nothing. A BSD, illumos or Haiku image takes the repository as a read-only disk, and the rescuer guest still does the backup and verify.

**What the release covers.** For the six non-Linux systems the release image is a separate build from the `--test` image, whose serial console is the only way to drive a boot, so `release` passes on the `--test` build and says so per image (`release build not tested`). Linux's two images share a squashfs, which `release-same` checks.

**First results** (2026-11-10/11, test worlds on both hosts): boot tests passed on all seven images. The Linux panel passed 5 of 6; nixos2605 failed because load formatted its bcachefs with the image's tools at on-disk version 1.39 where the source's was 1.13, so the restored disk booted but the host's verify couldn't mount it, a finding for load (one-kernel-rescue.md's "recreate the source's feature sets"). Load now formats at the source's version_min (e2b31ad), and nixos2605+img passed on both hosts on the image of 2026-11-11 built with it. On images rebuilt from the fixed builders, freebsd14, netbsd11, dragonfly6, omnios58 and haiku6 passed; openbsd8 failed because rustic-bko was killed out of swap during the restore (a 3 GiB rescue VM with no swap; rustic_core's restore readers), a finding outside the harness. The predicted image defects were fixed rather than classed (`/mnt/bko-target` on the read-only roots, the stick known as the boot medium by its config partition on every system, DragonFly's `part-by-label/` and FreeBSD's bsdlabel root, OpenBSD's 64 MiB `/root`), and so were four the runs found: a tmpfs on FreeBSD's `/mnt` for `zpool create -R`, NetBSD refusing bko-holt's second open of a block device, illumos importing the image's own disk as the pool (the disks' serials), and a key-file umask that reached the Linux rebuild's chroot. On the images of 2026-11-07, built before those fixes, only haiku6 passed.

**Cost.** 25 + 39.6 (4.75 + 4.18 + 6.33 + 15.38 + 5.62 + 3.35) = **~65 gm**, ~25 min, ~0 GB; the build's estimates give 12.7 gm for the seven boot tests and 64.7 gm for the 12 panel cells (13 and ~70.3 gm with freebsd15-geli+img's 5.6, 2026-11-14). Measured: boot tests 0.8–2.7 gm an image; the Linux panel's cells 3.0–6.8 gm (~27 gm for the six), the other systems' passing cells 3.7–14.9 gm.

### BkoTest2 and SweepTest2

**"BkoTest2"**, the everyday word, runs the due cells of GateTest2, GuestTest2, SticTest2 and HoltTest2, and ImgTest2 for new images, in one queue across the run's hosts (testvm/hosts.json's default hosts, chopin and cinqcent, unless `--hosts` names others; testvm/HOSTS.md). When nothing changed it runs only GuestTest2's 7-day `--try` guests, if any. Large HoltTest guests wait for RAM-disk room, other sessions' reservations included.

**"SweepTest2"** runs GateTest2All, GuestTest2All, SticTest2All and HoltTest2All fresh, changed or not, on the user's word; each report shows the days until it is due (14 by default, decision 3). A new verdict on unchanged inputs means a key misses an input or a cell is flaky. **Cost:** 79 + 282 + 247 + ~30 ≈ **~640 gm**, ~2.5 h, 0–5 GB with the big guests taking turns in RAM (est.), where the three Test1 families wrote 227 GB on 11-06 [cost (e)].

**As built** (2026-11-10). The sweep takes in the guests built since this draft: GuestTest2All's 51 and HoltTest2All's 49 sources include endeavouros, endeavouros-grub and manjaro (and ubuntu26-luks), which have no SticTest check yet; the reports' `coverage:` lines name every guest with no check or no spec entry. The build's own plan, `t2 SweepTest2 rel4 plan` on an empty ledger (2026-11-11), comes to 110 + 281 + 263 ≈ **~655 gm**, ~2.2 h, with the gate's layers (~0 gm, ~68 min, est. 1.7 GB of build cache) beside them.
- **BkoTest2** takes one lock for every family, pulls once, probes the facts once and makes one private copy. It plans every child, checks the identity of the guests its stic and holt cells need (a guest that fails gets a FAIL row and runs nothing), plans again, then runs SticTest2, HoltTest2 and GuestTest2 side by side, the gate's remaining layers after them (what the children's gate steps already ran isn't run twice), and ImgTest2 last, on the rows just written. Run-wide stops (harness results, swap-out, the write budget) reach every child; a per-family stop stays in its family. `BkoTest2 plan` prints each child's plan and one combined total in ~2 s; with nothing due each child prints its line, then the sweep line.
- **SweepTest2** is the same conductor with the four All forms (rule `fresh:Sweep`, 5 GB for the whole sweep), and no ImgTest2: the sweep doesn't test images, so its report lists the soft-stale images and each panel's age. The gate runs once. It writes `sweep.json`, complete only when every child ended with no stop, none was refused and every due cell has a decisive row; an incomplete sweep doesn't reset the days until the next is due. The days are 7 until 14 days after the first complete sweep (the shadow weeks), then 14.
- Both resume as one run (`t2 resume t2b-…` or `t2w-…`): children with a report are skipped, the rest resumed or started.

## What changes in Test1

- The Test1 words keep their scope and their own resume rule (SticTest1's `--logs-mark`), never skip from the Test2 ledger, and write ledger rows.
- W1 changes ht1.sh, so HoltTest1 gets HoltTest2's write path and shipped rustic-bko, with the same checks (decision 10).
- No longer routine: a Test1 rerun after each change; GuestTest1's own SticTest round (199 gm); `--status` hashing outside GuestTest2All; whole-disk compare.py `!` rounds.
- After two shadow weeks of weekly sweeps checked against Test2's picks, the user decides whether the Test1 words become aliases of the All forms.

## Shared pieces to build (ordered)

**Runs today with no new code:**
- **HoltTest by hand:** HT_RAM=1 only under `cmp-run.sh --reserve D 4`, D from r3all's left size plus overlays (pop24 est. ~17 GiB), never the queue runner's `--reserve 0 4` (ht1-queue.sh:60). Delete after a pass; after a holt fix that leaves dump alone, run only `rebuild verify`; pick guests from holt.md (c)'s map; after a judge-only change, re-judge r3all's kept dumps.
- **Cheap layers:** cargo test, redact_dumps, fork-tests.sh, run-all.sh's linux and sparse steps, readelf against FreeBSD 14.5.
- **SticTest and GuestTest by hand:** drive.py on chosen guests, skipping those whose SHA256SUMS and generations match their last pass; `--try` on changed guests only, beside the image. Since V1, `VM_UPDATE_RAM=GIB` puts the overlays in the RAM disk only under a reservation of the process's own (`cmp-run.sh --reserve`), and a watcher stops the VM past it; before V1 its overlays filled the tmpfs on 11-04.

| # | Piece | Effort (est.) | Gains |
|---|---|---|---|
| W1 (done 2026-11-08) | ht1.sh deletes on a pass and moves a failed run's state to /bkoscratch; HT_RAM=1 under reservations from r3all's sizes, then logged peaks; ht1-queue.sh into the repo; HT_RAM for ht1-offline.sh (lines 24-34 write /bkoscratch only); the shipped rustic-bko; zfsmirror and aerynos scratch in RAM | 2–2.5 d | ~200 → ~103 GB a run, or ~0–5 GB; no full disks (r32) |
| W2 (done 2026-11-08) | WEAR_TAG through cmp-run.sh and iolog.sh; wear-report.py groups by tag, not first word (:81); swap-out logged | 0.5 d | the untagged 59%; budgets |
| V1 (done 2026-11-10) | vm-update: `--try` in RAM under a reservation, without trim; the merge rule; fleet lock; size, mtime, inode at merge; post-update dump; the merge gate, hooks, `--probe`, fingerprints | 2 d (4 d at the build's estimate) | 3–8 GB a run; true "changed" keys; fingerprints |
| G1 (done 2026-11-09) | GateTest2 runner over today's layers, plus binary checks | 1.5 d | #3, #8, #10 at the gate |
| L1 (done 2026-11-09) | Ledger and planner: keys, due and carry rules, `plan`; writers in drive.py, the ht1 drivers and vm-update; helper manifests for 41 checks (18,413 lines [stic (a)]); private-copy runs; sync-hosts.sh records its commit; class table; self-test | 5 d | skipping |
| H1 (done 2026-11-09) | Dump corpus (45 dumps, via redact.rs; 49 since), golden plans, invariants, plan diff, Rust diff driver in .gitattributes | 1.5 d | load selection; #12, #16 |
| I1a (done 2026-11-10) | The Linux image as rescuer: a per-run key typed on the `--test` image's serial console (no image carries one; f2fs-tools, nilfs-tools and 9p were already in it); manifests; pins; ImgTest2 for Linux; img-one.sh into stick/ | 2 d (2.5 d at the build's estimate) | first loads from a real image |
| — (done 2026-11-10) | The words GuestTest2, ImgTest2, BkoTest2 and SweepTest2 over V1, P1 and I1: planner arms, runners, the conductor, composite reports | 4.5 d at the build's estimate (core 2.5, conductor 2) | one queue for every family; the sweep |
| | *Phase 1 ≈ 14.5 d, W1 and W2 first (~3 d)* | | |
| G2 | Gate filesystems, cases and Linux fixture union; self-tests for 15 judges; re-judging | 3 d | #2, #4, #9's holes, #11 at the gate |
| P1 (done 2026-11-10) | GuestTest2 image checks (built in the `--try`'s first boot, with no vm-mount and so no name locks; a probe boot for haiku6), after-reboot checks, new-guest step, log baselines | 3 d (5 d at the build's estimate) | #18–20 earlier |
| H3 | `+close` throttled under load, variant words, variants inside the base run | 1 d | #14's kind at bring-up |
| M1 | affected.sh v2: per-OS digests (go list -deps, cargo dep-info), Haiku, version-only and Cargo.lock-pin rules; holt module map; old rows re-keyed | 2 d | one-OS changes stay on that OS |
| G3 | OS-gated fork tests in the build guests | 1 d | #7-type, earlier |
| R1 | Refresh the one-time OpenBSD, DragonFly and illumos rescuers (ht1-bsd.sh:179-264) by generation | 0.5 d | rescuers follow OS updates |
| | *Phase 2 ≈ 10.5 d* | | |
| I1b (done 2026-11-10, ahead of phase 3) | BSD, illumos and Haiku images as rescuers, and the image defects they found | 2 d (3–4 d at the build's estimate) | non-Linux loads from real images |
| H4 | Loop layer (layload.md:536-541) in a RAM rescuer VM; partition and format replay; synthetic layouts (LUKS1, raidz, md RAID0/6/0.90, 4K sectors, XFS external log) | 4–6 d | #15 before any guest |
| C1 | Backup cache keyed by generation, rustic-bko, backup harness and dump code | 1.5 d | −1.6 gm per cached cell (decision 7) |
| S1 | Cross-tool knob passed by drive.py; one rt_nodes walk per snapshot; validated on its 38 guests (est. ~250 gm, once) | 2–3 d | est. −40 gm per full round |
| | *Phase 3 ≈ 11 d; ~36 d in all* | | |

## Scenario costs vs Test1

Each figure reads wall time / gm / SSD GB. Test1's are measured [cost (a), (e); change (b)]; Test2's are estimates.

| Scenario | Test1 | Test2 | Test2 arithmetic |
|---|---|---|---|
| (i) Nothing changed | SticTest1 37–65 m / 200–282 / 0–14; all three on 11-06: 1 h 47 m / 510 / 227 | 10 s / 0 / 0 | ledger lookups (GuestTest2's 7-day `--try`s aside) |
| (ii) FreeBSD attribute code | SticTest1 40–65 m / 282 / ~14 | ~30 m / ~40 / ~0 | 31 + HoltTest freebsd15 4.1 + ~5 build-guest tests; before M1, as (iii) |
| (iii) OS-independent fork change | SticTest1 40–65 m / 282 / ~14, no whole machine | ~65–85 m / ~320 / ~0 | fast gate 2–10 m, then 238 + 52 + ~30 OS-gated; with decision 1, 261 |
| (iv) bko-holt fix | HoltTest1All 90 m / 191 / ~200 | in plans ~15 m / ~28 / ~0; not ~1.5 h / ~216 / 0–103 | 7 XFS plans; exec.rs: all 45; ≤39 of canaries for carried platforms before the next image |
| (v) Fleet OS update | round ~30 m, 50–85 GB; then 1 h 47 m / 510 / 227 | ~75–105 m / ~215–310 / ~0; merges ~25–40 GB | round checks ~80 + SticTest2 ~40 + HoltTest2 20–40 guests × 4.65; dragonfly6's cells cover its rebuilt rustic-bko |
| (vi) 5 new RAID guests | raid1 26 m / 53 / ~40, then raid2–6 (omnios 140 gm, 16.6 GB) | ~35 m / ~60 / ~1, plus debugging | first passes ~30, variants ~10, `+close` ~10, GuestTest2 ~12; debugging in RAM (ht1-debug.sh, 38–91 s a rebuild) |
| (vii) Judge or plumbing change | r3all after two judge rules: 90 m / 191 / ~200; st2a after 39c9416: 58 m / 201 / ~12 | judge 5 m / 0 / 0; plumbing ~25 m / ~70 / ~0 | re-judge; canaries 54.6 + ~15 HoltTest (debian13, ubuntu26-uefi, freebsd15, redox) |
| (viii) New rescue image | boot tests only, never a load | boot tests; + ~25 m / ~65 / ~0 if storage pieces changed | 25 + 39.6 |
| (ix) Full sweep | three families 1 h 47 m / 510 / 227 | ~2.5 h / ~640 / 0–5 | every All form |

In (iii) Test2 costs more than SticTest1 and takes est. 10–20 min longer, but it adds whole-machine restores (which found #11) and writes nothing. (ix) is slower but runs every 14 days, not nightly.

## Coverage replay (yield.md #1–#20)

| # | Test2 catcher | Needs | vs Test1 | gm |
|---|---|---|---|---|
| 1 | SticTest2's Linux cells (mint22, pop24, zorin18, fedora44-uefi); the musl check blocks a repeat | G1 | same | ~5 |
| 2 | gate: 16/64 KiB xattrs | G2 | earlier | 0 |
| 3 | gate: fs_roundtrip's node identity, which exists today [cheap (a)] | G1 | earlier | 0 |
| 4 | gate: changed hard link; else mint22, zorin18, pop24, mx25 | G2 | earlier | 0 |
| 5 | fedora44-uefi, rocky10, tumbleweed (SELinux; the hosts have none) | — | same | 9.4 |
| 6 | ubuntu24 (OpenZFS 2.2.2) | — | same | 16 |
| 7 | omnios58; illumos OS-gated tests | G3 | same or earlier | 6.2 |
| 8 | gate: vfat and XFS against the baseline; else fedora44-uefi | G1 | earlier | 0–4.5 |
| 9 | gate: bcachefs holes at 512 B; casefold: nixos2605 | G2 | earlier (holes), same (casefold) | 0–3.9 |
| 10 | gate: FreeBSD 14 symbol versions | G1 | earlier, at build | 0 |
| 11 | gate: near-full zstd btrfs; omarchy on the rustic-bko panel | G2 | earlier | 0–9.4 |
| 12 | gate: plan invariants on the first dump | H1 | earlier | 0 |
| 13 | rescuer rule: debian13-stack | I1a for the image's kernel | same | 3.4 |
| 14 | `+close` at bring-up (both guests were new on 11-05; r2all found it 11-06); FLAKY-FAIL | H3 | likely earlier; a rare race can pass both | ~10 |
| 15 | the new guests' first HoltTest2; format replay and loop layer | H4 | same; earlier with H4 | ~12 |
| 16 | gate: "/ is mounted" on the new dump | H1 | earlier | 0 |
| 17 | HoltTest2 per new OS; ht1-debug.sh in RAM | — | same; ~0 GB, not ~465 GB (11-05 [cost (a)]) | as today |
| 18 | GuestTest2: loader default, newest kernel running | P1 | earlier | ~1 |
| 19 | GuestTest2: package sources | P1 | earlier, at build | 0 |
| 20 | GuestTest2: fstab mounts mounted, not shadowed | P1 | earlier (11-05, not 11-06/07) | ~1 |

With every piece built, eleven findings come earlier, four earlier or the same (#7, #9, #14, #15) and five the same; none is missed. Phase 1 alone brings five earlier (#3, #8, #10, #12, #16). The gate cases for #2, #4, #11, #12 and #16 were written from those findings, so the replay shows they catch a repeat, not that they would have found the first. #13's cause is an inference [yield, gaps].

## Risks and mitigations

- **A key misses an input** (mirrors, timing, other sessions, host state): soft host keys, canaries for carried binaries, the sweep flagging new verdicts on unchanged inputs, the ledger self-test, unknown paths mapping to "all".
- **A class hides a cell** (non-Linux now, Linux too after decision 1): eligibility, fan-out, rotation, the sweep.
- **Races:** 20 clean closes only bound a race below ~15% (est.), so a rarer one can pass Test2 and the sweep alike.
- **RAM pressure:** a desktop guest needs ~16–18 GiB of the 30 GiB RAM disk and zfsmirror est. 17–22 GiB; waits of 45–75 min for room have happened on the shared hosts [yield (a) #22]. Measured reservations, the swap stop rule and BkoTest2's ordering limit it.
- **Ledger and harness bugs** (harness changes gave the most FAIL lines [yield (d)]): every skip prints its reason, the shadow weeks compare picks with sweeps, and W1 and W2 change where runs write, not how they judge.
- **Re-baselines:** M1 re-keys old rows, since per-OS digests can be computed for old builds; S1 changes what 38 checks run, so it reruns them once.
- **What is lost:** nightly full runs, per-member proof between rotations, and restored disks after a pass (est. ≤0.5 GB of records stays a run; a failure keeps everything).

## Decisions for the user

Decided so far: 2 and 10 (2026-11-07); 1, 3, 4 and 5 as recommended (2026-11-08, built into L1, G1 and H1); 8, 9 and 11 as recommended (2026-11-09, built 2026-11-10). 6 and 7 weren't needed for that build: the cross-tool directions stay as the checks run them, and there is no backup cache.

1. **"Every Linux guest gets its own pass" (11-01).** **Decided 2026-11-08: as recommended.** Test2 keeps it. Representatives would cost 179 gm, not 238, per change reaching every OS (est. 10–15 min less), with the Linux classes {debian13, devuan6, mx25, debian13-stack, debian13-zfs}, {ubuntu26-zfs, ubuntu26-zfsmirror}, {rocky10, rocky10-lvm} and {arch, artix-s6}, fedora44 in sweeps only, and the other 15 Linux guests and sculpt2610 alone [design-classes §2.0]. *Recommend:* switch once G2 lands; no second guest of a class ever found more [yield (c)]. #5 needs enforcing SELinux, which the hosts lack, so fedora44-uefi, tumbleweed and the rocky10 class stay on their own.
2. **RAM before disk.** **Decided 2026-11-07: RAM first.** On 11-04 the user chose disk thrash for speed; since 11-06 /bkoscratch is the P3, so "disk first" and "never the P3" conflict (work-in-ram.md, testvm-chopin.md). *Recommend:* RAM first under reservations, delete on a pass, the 6 largest HoltTest guests and zfsmirror taking turns in RAM (est. +30 min a sweep, ~103 GB less a run), no routine compare.py `!` rounds. The P3 might wear ~4× slower (est., from 67 GB written outside HoltTest1 windows in ~12 h [cost (c)]).
3. **Sweep cadence.** **Decided 2026-11-08: as recommended.** *Recommend:* weekly in the shadow weeks, then every 14 days and before each image release, on the user's word.
4. **Canaries after a plumbing change**, the rest canary-verified until they ride along or the sweep comes. **Decided 2026-11-08: as recommended.** round-speedups.md makes this the user's call; its "final round runs everything" becomes SweepTest2. *Recommend:* yes (~70 gm against ~200).
5. **What an OS update triggers.** **Decided 2026-11-08: as recommended.** *Recommend:* HoltTest2 on any kernel, boot-chain or storage-package change; SticTest2 when the kernel line, ZFS, bcachefs, the LSM, or the mkfs and attr tools move. A dump lists ~20 such packages (fedora44-uefi's: 20), so HoltTest2 may fire on 20–40 guests a round (est. 93–186 gm); narrow it to minor versions if it fires on most guests for two weeks.
6. **Cross-tool directions on guests** (needs S1). *Recommend:* all four at the gate every time; on guests in sweeps and when the repository format, node encoding or that OS's attribute code changes, so node parity and cross-restores stay tested (cross-tool-compat-courtesy.md).
7. **Backup cache** (~40 GiB per fleet refresh, or 1.6 gm more per cell without it). *Recommend:* last, and only if the ledger shows frequent reruns at one generation.
8. **ImgTest2 as the rescue-image release gate.** **Decided 2026-11-09: as recommended**, with I1a's Linux part and I1b both built (2026-11-10). *Recommend:* yes, with carried PASSes and their canaries (est. ≤42 gm), and with I1's Linux part in phase 1, since until then the BIOS rescuer panel tests Debian 13's mkfs tools, not the image's.
9. **Bootstrapping.** **Decided 2026-11-09: as recommended.** The baseline sweep runs after the build lands, once `t2 guest-fingerprints` has probed the guests (est. ~40 gm), and isn't any piece's test; the build's plan puts it at ~655 gm. *Recommend:* one baseline SweepTest2 on the new write path (~640 gm, ~2.5 h, est. 0–5 GB), which also measures RAM-disk peaks, rather than old runs' inputs inferred from timestamps.
10. **HoltTest1's definition.** **Decided 2026-11-07: yes; built as W1, landed 2026-11-08.** The user's reviewed HoltTest1D backs up into /bkoscratch/holttest1d/RUN/GUEST/share/repo and keeps everything on a failure (holttest1d.md). W1 moves the run into /mnt/bko-ram; a pass keeps logs, dumps, plans, report and compressed fsmeta, as the plan already said; a failure moves its state to /bkoscratch before the diagnostic rerun, since a failed desktop guest pins ~16 GiB of Chopin's 28.9 GiB budget. *Recommend:* yes, one write path for HoltTest1 and HoltTest2.
11. **HoltTest2's family lists.** **Decided 2026-11-09: as recommended**, and applied to the guests built since: endeavouros, endeavouros-grub and manjaro join 2A (and ubuntu26-luks, 2026-11-10, 2U). HoltTest1's README marks them "(1All only)": that row is about HoltTest1, this decision about HoltTest2's letters. By the user's wording the letters take later guests of their systems: 2U gains ubuntu26-zfs, and 2BSD gains freebsd14 and ghostbsd26-zfs. freebsd15-ufs (the FreeBSD rescuer) and freebsd15-geli (nothing types its GELI passphrase, and holt has no GELI) stay out as sources [holt (b)]; devuan6 stays in no family. *Recommend:* yes. *Note 2026-11-14:* freebsd15-geli is a source since bko-holt has GELI and ht1-bsd.sh types its passphrase at the loader through console-boot.py (docs/design/geli.md, G6): it joins 2BSD, with a corpus dump and a golden plan; freebsd15-ufs still stays out.
