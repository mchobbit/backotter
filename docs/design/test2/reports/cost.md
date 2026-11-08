# Cost of past test runs (researcher: cost)

**Method.** Disk writes come from sysstat on both hosts, read with `sadf -d -- -d` from `host:/var/log/sysstat/saDD`. It takes a sample every 10 minutes. Cinqcent has samples from 2026-11-03 18:50Z; Chopin only from its reinstall boot, 2026-11-06 17:30Z.

I checked sysstat against two other sources:
- **Against `/proc/diskstats` since boot:** Chopin's P3 247.7 vs 248.7 GB, Cinqcent's sda 261.5 vs 261.6 GB, Cinqcent's nvme0 259.7 vs 276.8 GB.
- **Against SMART:** sysstat read 89% of SMART's figure on Cinqcent's nvme0 (1,907 vs 2,152 GB over 3.3 days) and 98% on its sda.
- **jobs.log against SMART** (Cinqcent, 11-07 04:03–09:00Z): 16.64 vs 16.87 GB.

So the sysstat figures are lower bounds, within about 10%. All times are UTC. Older SMART readings come from session transcripts in `/Users/mchobbit/.claude/projects/-Users-mchobbit-git-backotter/`. Parsed copies are in `/private/tmp/claude-501/-Users-mchobbit-git-backotter/6e7b60cc-f873-4450-892c-1b053e7a5606/scratchpad/t2/cost/` (`sar/`, `st1_marks.tsv`, `ht_rows.json`, `r3all_files.tsv`).

## (a) Per run

**SticTest1** (`host:~/backotter-work/stictest1/GUEST/logs/MARK/run.out.{start,end,exit}`). Chopin has no write data for these dates.

| Mark | Start | Guests Ch/Cq | Wall | Guest-min | Fails | Cinqcent nvme0 writes |
|---|---|---|---|---|---|---|
| st1-st2a | 11-04 13:24 | 16/15 | 58 m | 201 | 0 | 11.9 GB, all in 13:30–13:40, ubuntu26-zfsmirror's slot |
| st1-rel1 | 11-05 03:54 | 14/17 | 42 m | 209 | 1 | 13.1 GB, source not identified (zfsmirror ran on Chopin) |
| st1-rel2 | 11-05 07:43 | 16/16 | 37 m | 201 | 0 | **0.0 GB** (zfsmirror ran on Chopin) |
| gt1-rel2 | 11-05 11:02 | 15/16 | 37 m | 199 | 0 | 14.2 GB, all in 11:10–11:20, zfsmirror's slot |
| st1-rel3 | 11-06 08:03 | 0/14 | 64 m | 131 | 4 | 31.7 GB, mixed with other work |
| st1-rel4 | 11-06 21:53 | 18/18 | 65 m | 251 | 0 | can't be separated from r3all |
| rel4t/g/n | 11-06 22:57 → 11-07 02:36 | 8 | 9–20 m each | 57 | 0 | — |

In total, SticTest1 used 1,379 guest-minutes (23 guest-hours) in 2.5 days.

**HoltTest1** (`host:/bkoscratch/holttest1d/RUN-GUEST.log` headers and mtimes, `queue/RUN/runner-HOST.log`, sysstat). Neither root disk took more than 1.1 GB in any of these windows.

| Run | Start | Runs (pass) | Wall | Guest-min | Chopin P3 | Cinqcent nvme0 |
|---|---|---|---|---|---|---|
| Development runs (ht1\*, fin\*, r1d–r31, not r1all) | 11-05 13:22 → 11-06 06:53 | ~203 | — | ~1,530 | unmeasured | ~465 GB inside HoltTest1 windows on 11-05 alone |
| r3d (HoltTest1D, 6 runs) | 11-05 17:08 | 6 (6) | 3 m | 15 | — | 6.9 GB (3 runs) |
| r1m | 11-05 17:16 | 4 (4) | 7 m | 28 | — | 12.4 GB (2) |
| r3a | 11-05 17:49 | 8 (8) | 10 m | 20 | — | 3.4 GB (3) |
| r1all | 11-05 18:06 | 39 (16) | 29 m | 139 | — | 44.3 GB (19, most stopped at the OS gate) |
| r32 | 11-06 07:07 | 36 (27) | 58 m | 290 | — (the old SK900 filled up) | 126.1 GB (19) |
| r2all | 11-06 10:09 | 46 (40) | 91 m | 263 | —; free space fell 839→693 GB | 6.3 GB (5) |
| **r3all** | 11-06 21:53 | 40 (40) | 90 m | 191 | **133.3 GB** (26) | **77.7 GB** (14; about 14 GB of it is SticTest1's zfsmirror at 22:20) |
| raid1 | 11-07 02:41 | 9 (4) | 26 m | 53 | 30.5 GB (8) | 9.4 GB (1) |
| omnios raid4–6 | 11-07 04:52–08:20 | 6 (3) | — | 140 | — | jobs.log: 3.2–3.35 GB per omnios58 run, 4.3–4.7 GB per omnios58-mirror run |

In total: 379 HoltTest1 runs, 41.5 guest-hours, in 39 hours.

**GuestTest1** (`host:/bkoscratch/gt1/try-*.log`, `host:/bkoscratch/build/gt2-*.log`, `vms/*/update.log`; total times from guesttest1.md)

| Run | `--try` windows | End to end | Guest-min | Writes |
|---|---|---|---|---|
| 1 (11-05) | Chopin 10:37–11:00 (14 guests, jobs 3); Cinqcent 10:37–10:49 (18 plus dragonfly6) | ~1 h 40 m | at least 60 for `--try`, plus 199 for its SticTest1 round gt1-rel2 | Cinqcent `--try` 7.8 GB; Chopin unmeasured (overlays 26.8 GiB apparent); gt1-rel2 14.2 GB on Cinqcent |
| 2 (11-06) | Chopin 21:38–21:52 (19, jobs 2); Cinqcent 21:37–21:48 (20) | ~1 h 50 m | at least 49; SticTest1 reused rel4's results | Chopin 5.1 GB (sysstat covered 84% of the window), Cinqcent 3.0 GB; overlays 14.2 GiB apparent |

## (b) The most expensive and the cheapest guests

**SticTest1, time** (median of up to 5 full rounds):
- The 10 slowest take 51% of a round's ~282 guest-minutes: ubuntu26-zfs 16.2 min, ubuntu24 15.9, debian13-zfs 14.3, mint22 13.6, freebsd15 12.5, freebsd15-geli 12.4, ghostbsd26-zfs 12.4, freebsd14 11.9, mx25 11.9, ubuntu26-zfsmirror 10.4.
- Guests with a ZFS root take 38% of the round; the 7 FreeBSD-family guests take 24%.
- The cheapest: sculpt2610 0.8 min, haiku6 0.9, rocky10 1.8, void-musl 1.9, debian12 1.9, aerynos 2.2, rocky10-lvm 2.2.

**SticTest1, GB:** almost all of it is one guest. ubuntu26-zfsmirror writes about 14 GB per run, measured twice. Its check puts a 24 GiB raw spare disk on /bkoscratch with `cache=unsafe` (`testvm/stictest1/ubuntu26-zfsmirror/check.sh:63-64,78`). aerynos also uses scratch on /bkoscratch (`aerynos/check.sh:30,37`); I couldn't measure it. Every other check runs in tmpfs (for example `debian13/check.sh:23-26`), and a 16-guest round without zfsmirror wrote 0.0 GB (rel2).

**HoltTest1, time** (r3all): the slowest were dragonfly6 15.6 min (the critical path), omarchy 9.6, mint22 9.3, pop24 9.2, ubuntu26-zfs 7.8, zorin18 6.4 and openbsd8 6.3. The cheapest were alpine324 2.0, artix-s6 2.4, redox 2.6, arch and cachyos 2.7, and debian13, devuan6, void-musl and debian12 at 2.8.

**HoltTest1, GB:**
- These figures are what each guest left in r3all (restored disk plus repository), in GiB. Bytes written are about 1.18 times what is left: omnios58-mirror raid6 wrote 4.70 GB and left 3.98 GB; Chopin's r3all Linux guests wrote about 117 GB and left 99 GB.
- The 7 largest hold 61% of the 146 GiB left: pop24 16.3, omarchy 15.4, mint22 13.9, mx25 12.4, ubuntu26-zfsmirror 11.8, zorin18 11.6, ubuntu26-uefi 8.5.
- The cheapest: sculpt2610 0.15, redox 0.34, alpine324 0.52, artix-dinit and artix-s6 1.5 each, debian13 1.7.
- The BSD, illumos and Haiku guests (`ht1-bsd.sh`) write about 2.3 GB each: 19.7 GB for the 1-slot stretch 22:08–22:46 holding 7 of them plus parts of two Linux runs. omnios58 measured 3.2–3.35 GB in jobs.log.
- Re-run most often: omnios58 18 runs (6.4 h), debian13-stack 18, mint22 17, ubuntu26-zfsmirror 13, alpinebsd 12.

**GuestTest1** (`--try` overlay sizes, apparent MiB; real writes are about 0.55 times these):
- Run 2 / run 1: zorin18 1031/3771, nixos2605 1030/549, tumbleweed 348/2882, ubuntu26-zfsmirror 362/2728; run 2 only: rocky10-lvm 491, rocky10 469, openbsd8 466.
- The smallest in run 2: hardenedbsd15 7 (2855 in run 1), void-musl 8, alpine324 9, devuan6 9.
- Time inside the guest: openbsd8 3.0 min, zorin18 2.5, rocky10 2.4; most about 1 min.

## (c) Disk wear

Rated endurance is the vendor's figure as I recall it; I didn't check it in this session.

| Disk | Written in its life | % used | Rated | Measured rate | Life left at that rate |
|---|---|---|---|---|---|
| Chopin nvme0n1, /bkoscratch: Crucial P3 CT2000P3PSSD8, 2 TB QLC | 50.70 TB, 7,908 h | 6% | 440 TBW | 11-01 → 11-07: 3.08 TB in 5.5 d = 0.56 TB/d (0.34 TB/d without the 1.21 TB fio test); after the 11-06 21:32Z boot: 0.51 TB/d | 389 TB left: about 2.1 years at 0.51 TB/d, 3.1 at 0.34 |
| Chopin nvme1n1, root: WD SN770 1 TB | 25.83 TB, 416 h | 1% | 600 TBW | 1.45 TB from 11-03 to 11-07, all migration (671 GB on 11-06 17:30–21:32); since then about 2.6 GB/d | not a limit |
| Cinqcent nvme0n1, /bkoscratch: WD SN770 1 TB | 26.57 TB, 503 h | 1% | 600 TBW | 2.15 TB from 11-03 19:39 to 11-07 03:38 = 0.65 TB/d (sysstat days: 596, 725, 487 GB) | 573 TB left: about 2.4 years |
| Cinqcent sda, root: Samsung 870 EVO 1 TB | 25.45 TB, 27,037 h | 1% | 600 TBW | 0.72 TB in the same window = 0.22 TB/d, 99.5% of it outside any test; about 1.8 GB/d since 11-06 evening | not a limit |
| Chopin's old SK900-512GB, the old /bkoscratch (removed 11-06) | 22.4 TB at 28,631 h | 2% | unknown | 0.68 TB from 11-01 21:13 to 11-03 15:26 = 0.39 TB/d, including a 262.5 GB copy of vms | — |

Where the SMART readings come from:
- transcript 39d24434: 11-01 20:06Z and 21:13Z;
- workflow wf_362b9482 (agent-a1533d): 11-03 15:30Z;
- transcript 9ac8a383: 11-03 19:39Z;
- transcript 0c05615e: 11-06 07:54Z and 08:10Z, 11-07 03:38Z;
- `host:/bkoscratch/wear/smart.log`: 11-07 04:03–09:00Z.

By the drive's own counter, the P3 uses about 8.4 TB per 1% of wear, which would give about 4.4 years at 0.51 TB/day. Both SN770s already had 24.3 TB at 412–453 power-on hours on 11-03, about 1.3 TB a day each before then; I can't attribute those writes.

**Job tags:** jobs.log only starts 11-07 04:02Z.
- On Cinqcent, "reserve sudo" wrote 16.6 of its 17.2 GB. That tag is actually `ht1-bsd.sh omnios58[-mirror] raid4–6`: `wear-report.py:81` groups a job by the wrapper's first word.
- On Chopin, only `build-holt.sh`, at most 0.1 MB per job.

**Before the wear log, by sysstat window:**
- Cinqcent's /bkoscratch wrote 1,926 GB from 11-03 18:50Z to 11-07 09:10Z: 687 GB (36%) inside HoltTest1 windows, 182 GB (overlapping) inside SticTest1 windows, 11 GB inside GuestTest1's, and 1,127 GB (59%) outside every test window.
- Chopin's P3 after its 21:32Z boot, with no migration running: 248 GB, 181 GB (73%) of it inside HoltTest1 windows.

## (d) What dominates the writes

1. **Host reinstalls and migrations, the largest single writes.**
   - Chopin, 11-06 17:30–21:32: 556 GB to the P3 and 671 GB to the root disk.
   - Cinqcent's sda: 313 GB on 11-03 (reinstall restore) and 585 GB on 11-06 (12:00–18:30).
   - Moving Cinqcent's ~/backotter-work onto its nvme0: 71 GB.
   - The P3's heatsink fio test: 1.21 TB.
2. **Whole-disk compare.py rounds,** which belong to none of the three families. All three were on 11-04 on Cinqcent, with restored disks under /bkoscratch/compare-disks:
   - u3, 15:30–16:28: 239 GB for 10 guests;
   - u2c: 93 GB for mx25 and omarchy;
   - u3b: 49 GB for 2 guests.

   That is about 45–50 GB per large guest.
3. **HoltTest1's restored disks: 70% of what a run leaves behind** (102.3 of 146.4 GiB in r3all). Repositories are 27.5% (40.3 GiB), fsmeta JSON 1.7%, source overlays 0.6%, decoys 0.2%. On the Linux path, `ht1.sh` never deletes dst or the repository after a pass (`ht1.sh:437-440`); `ht1-bsd.sh` deletes them (`ht1-bsd.sh:694-696`).
4. **Real `vm-update` rounds:** about 50–85 GB on Cinqcent for the 11-06 09:10–09:40 round, which keeps its overlays beside the images, ships them and commits them.
5. **GuestTest1's `--try` overlays,** written beside the image (`vm-update:372-376`): 3–8 GB per host per run.
6. **SticTest1:** about 14 GB per round, all from zfsmirror's spare disk.
7. **Builds:** `build-holt.sh` writes at most 2 MB per job. Rust and Go builds weren't measured.

## (e) Baseline: one full run of each family today

| Family (last full run) | Wall | Guest-min | SSD writes | Left on disk afterwards |
|---|---|---|---|---|
| SticTest1 (rel4 plus rel4t/n, 41 checks) | 65 m for 36 guests (31–32-guest rounds took 37–42 m), plus 29 m for the last few | ~282 | ~14 GB plus aerynos (unmeasured) | logs only |
| HoltTest1All (r3all, 40 guests) | 90 m, the first 71 at 1 slot per host | 191 | **~200 GB** (Chopin 133 + Cinqcent ~64) | 146 GiB (92 GiB Chopin, 54 GiB Cinqcent) |
| GuestTest1 (run 2) | ~1 h 50 m end to end; `--try` 14 m | ≥49 | ~8 GB, plus ~14 GB if it runs its own SticTest1 round | 0 |
| All three, as run together on 11-06 21:37–23:24 | ~1 h 47 m | ~510 | **227 GB** (139.5 Chopin + 87.2 Cinqcent) | 146 GiB |

## Levers I noticed

- About half of HoltTest1All's bytes come from 6–7 desktop guests. `HT_RAM=1` (`ht1.sh:56,78`) already moves a run to the 30 GB RAM disk, and 30 of the 40 guests write 5 GB or less per run.
- SticTest1's SSD cost is one guest's spare disk. Its time goes to roughly ten ZFS-root, FreeBSD-family and Ubuntu guests.
- Repeated runs that changed nothing: SticTest1 rel2, gt1-rel2 and rel4 all passed every guest, within 40 hours of each other. HoltTest1 r2all and r3all ran 12 hours apart with 40 passes each.
- Work outside the three families (migrations, compare.py, real updates) wrote more than they did. A Test2 must not bring back whole-disk restores.
- sysstat already measures any run after the fact. `wear-report.py` should tag jobs by the test script, not by the wrapper.

## Gaps in this evidence

- Chopin has no per-interval write data before 2026-11-06 17:30Z. The old root has no sysstat files and the SK900 is gone. Every Chopin figure for st2a–gt1-rel2, r1all, r32, r2all and GuestTest1 run 1 is therefore unmeasured.
- The wear log (jobs.log, smart.log) started 2026-11-07 04:02Z. It holds no full run of any family.
- sysstat measures whole disks. Work running at the same time can't be separated: other sessions, rel4 beside r3all, rel1's unexplained 13.1 GB. Per-guest HoltTest1 bytes are estimates (left-behind size × ~1.18, calibrated on two points), and the BSD figure of ~2.3 GB comes from one 38-minute stretch.
- sysstat reads 2–11% below SMART, so its figures are lower bounds.
- I didn't verify the rated TBW figures, and the SK900's rating is unknown. Projections assume the recent rates continue. SMART counts host writes, not NAND writes.
- GuestTest1's per-guest times cover only the in-guest phase of `update.log`. Overlay sizes are apparent sizes.
- I didn't measure aerynos's SticTest1 writes, RAM-disk use or CPU, or the SN770s' first 24 TB.