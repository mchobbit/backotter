# Test2: a class-based test suite for BackOtter

**Sources.** Reports are in /private/tmp/claude-501/-Users-mchobbit-git-backotter/6e7b60cc-f873-4450-892c-1b053e7a5606/scratchpad/t2/reports/, cited by file name. Running kernels come from the r3all dumps in /private/tmp/claude-501/-Users-mchobbit-git-backotter/6e7b60cc-f873-4450-892c-1b053e7a5606/scratchpad/t2/change/dumps/.

**Units.** gm means guest-minutes. SticTest times are per-guest medians, 282 gm per round. HoltTest figures are r3all's. "est." marks an estimate.

## 1. Principles

1. **A pass stands while its inputs are unchanged.** Since 11-03, reruns of unchanged guests found only flakes and harness faults (change.md b).
2. **One representative per class is enough.** Duplicates found the same defect at the same moment: #3 on 10 guests, #1 and #4 on 4 each. No second guest of the same OS, FS and version ever found more (yield.md c).
3. **Any cell difference splits a class, fixture cases included.** A fixture case split #5, and OpenZFS 2.2 against 2.4 split #6 (yield.md c).
4. **The change picks the representative.**
   - Build and rescuer changes take the oldest member: freebsd14's libc for #10, the newer rescuer kernel's md for #13, and the write-side risk in one-kernel-rescue.md.
   - Logic changes take the member with the most cases.
   - Other changes take the stalest member.
5. **Test at the cheapest layer that sees the defect.** Ten of the 20 findings had a detector below whole guests: #2-4, #8, #10-12, #15, #16 and #19 (yield.md a). None of those layers runs routinely (cheap.md a).
6. **Spend guest time on new things, and repeat only to hunt races.** Every defect since 11-03 came from something new, or from a first run after a change (change.md b). #14 surfaced on r2all after earlier passes (yield.md a).
7. **Write only what a pass needs, RAM first.** HoltTest1All writes about 200 GB a run, and 70% of what it leaves on disk is restored disks it kept. SticTest1's ~14 GB is one spare disk (cost.md d, e).

## 2. The named tests

### 2.0 Classes

A **cell** is something the code branches on:
- **For the archivers:** platform binary × kernel line × FS and its mkfs options × LSM policy × OpenZFS version × need set.
- **For bko-holt:** the matrix in holt.md (b), confirmed by plan signatures (§3, item 4).

The tables mark each class:
- **≡**: truly redundant. One fixed member represents the class; the rest run only in sweeps.
- **~**: look-alikes that take turns.
- **≠**: they look alike but are split.

Rules:
- A selected class runs its stalest eligible member.
- Eligible means the member's recorded cells and need set equal the class's. A mismatch splits the class.
- A FAIL fans out to the rest of the class.
- A guest whose own image changed runs itself.
- Every run writes a ledger row (§3, item 1).

**SticTest2** (gm):

| Class | Members | Mark |
|---|---|---|
| A1: the hosts' 6.12.115, OpenZFS 2.3.9 and Debian 13 mkfs | debian13 8.0, devuan6 5.2, mx25 11.9, debian13-stack 9.2, debian13-zfs 14.3 | ~. HostTest2 covers their FS cells. One member per run adds real files. It splits if the kernels diverge. |
| A2: Ubuntu 7.0, OpenZFS 2.4.1 | ubuntu26-zfs 16.4, ubuntu26-zfsmirror 10.3 | ≡. zfsmirror's cells are a subset: encryption and mirroring sit below the datasets, and it has no file-pool run or real.sh (stic.md c). Running it only in sweeps saves ~14 GB a run. |
| A3: EL10, XFS, SELinux | rocky10 1.8, rocky10-lvm 2.2 | ≡ (LVM sits below XFS). |
| A4: 7.2.14, ext4 | arch 2.3, artix-s6 6.1 | ~ once their need sets match. |
| A5: JFS | artix-dinit 2.8, cachyos 4.9 | ≠ for now: kernels 7.2 and 7.3, different needs. |
| A6: Fedora 44, SELinux | fedora44-uefi 4.5, fedora44 3.5 | ≠ (#5). fedora44 is sweep-only until its fixture gains -uefi's cases. |
| B2: FreeBSD 15.1, ZFS | freebsd15 12.7, freebsd15-geli 12.4, ghostbsd26-zfs 12.5 | geli ≡ freebsd15 (GELI sits below the pool). ghostbsd26-zfs ~ (same 19 needs). |
| B3: FreeBSD 15.1, UFS | freebsd15-ufs 6.5, hardenedbsd15 6.8, ghostbsd26 6.5 | ~ (same 21 needs). HBSD's kernel and ghostbsd26's unjournaled md keep them rotating. |
| B4: NetBSD 11.1 | netbsd11 5.4, alpinebsd 6.7 | ~ since alpinebsd got the 11.1 kernel (11-07). |
| ≠ look-alikes | ubuntu24 vs A2: same check and 52 needs, but kernel 6.8 and OpenZFS 2.2.2 (#6). mint22 vs zorin18: same kernel and template, but f2fs vs nilfs2 | separate singletons |
| Singletons | debian12 1.9, ubuntu24 15.9 (OpenZFS 2.2.2), mint22 13.6, zorin18 9.8, ubuntu26-uefi 6.2, alpine324 2.6, void-musl 1.9, nixos2605 3.9 (bcachefs 512 B), omarchy 5.0, pop24 10.2, aerynos 2.2, tumbleweed 3.1, sculpt2610 0.8, freebsd14 11.9, openbsd8 5.7, dragonfly6 7.4, omnios58 6.2, haiku6 0.9 | Dumps: pop24 and omarchy run 7.2.15, aerynos 7.2.14 (stic.md is stale). |

One representative per class means 28 runs instead of 41:
- Linux: 9.7 (A1's mean) + 16.4 + 2.0 + 4.2 + 2.8 + 4.9 + 4.5 + 77.1 = 121.6 gm.
- The others: 57.3 gm.
- Total: **179 gm against 282**.

**HoltTest2** (r3all seconds / GiB left):

| Class | Members | Mark |
|---|---|---|
| C1: Debian, BIOS, ext | debian12 159/2.5, mx25 323/12.4 | ≡: same probe, mke2fs and boot path. mx25 runs only in sweeps; its boot-state check is empty and it writes 4.9× more. |
| C2: BIOS, ZFS | debian13-zfs 168/1.9, ubuntu24 229/4.5 | ~ for holt code. ≠ for rescuer changes, which take ubuntu24 (OpenZFS 2.2.2). |
| C3: Arch, ext4 | arch 146/2.4, artix-s6 134/1.5 | ~ (init differs) |
| C4: Arch, JFS | artix-dinit 167/1.5, cachyos 152/2.5 | ~ (init differs) |
| C5: Fedora boot, BIOS | fedora44 216/4.3, rocky10 254/2.8 | ~ for boot code. ≠ for probe and format (btrfs against XFS). |
| C6: FreeBSD, ZFS | freebsd15 247, ghostbsd26-zfs 265 | ~ until plan signatures check glabels |
| C7: UFS2 | ghostbsd26 239, hardenedbsd15 329 | ~ on the same condition |
| ≠ despite holt.md's duplicate list | freebsd14 (boot-environment clone), omnios58-mirror (legacy bootfs, #16), freebsd15-mirror | singletons |
| Singletons | debian13, devuan6, mint22 548/13.9, zorin18 373/11.6, alpine324, void-musl, nixos2605, tumbleweed, rocky10-lvm, rocky10-raid5, fedora44-raid, ubuntu26-raid, debian13-stack, ubuntu26-zfs, ubuntu26-zfsmirror 307/11.8, ubuntu26-uefi 338/8.5, fedora44-uefi, aerynos, pop24 550/16.3, omarchy 561/15.4, netbsd11, alpinebsd, openbsd8, dragonfly6 923, omnios58, haiku6, redox, sculpt2610 | |

That gives 38 representatives for 45 sources (freebsd15-ufs is a rescuer; freebsd15-geli waits on GELI). The layouts were built to be distinct, so HoltTest2 saves mainly through scope, caching, deletion and RAM.

**GuestTest2** classes are upstream streams:
- **Shared streams:** Debian 13 (3 guests), Ubuntu 26.04 (4), Fedora 44 (3), Rocky 10 (3), Artix (2), FreeBSD 15.1 (4), GhostBSD (2), OmniOS (2).
- **Singletons:** the other 22.

A canary shows whether a stream has updates. If it does, each member still runs `--try`, because their boot chains differ (#18).

### 2.1 HostTest2 (new)

**Purpose.** Catch everything a guest isn't needed for, minutes after a commit.

**Trigger.** "HostTest2" runs everything that needs no guest, on each of the run's hosts (testvm/hosts.json; default chopin and cinqcent), in /mnt/bko-ram. It also runs first inside every test below.

**Steps.**
1. holt's 243 cargo tests.
2. Golden plans and invariants over the dump corpus. The plan diff names HoltTest2's guests.
3. The fork suites, run as root on tmpfs.
4. Binary checks:
   - no NSS calls from static glibc;
   - symbol versions no newer than FreeBSD 14's;
   - the version string matches the commit.
5. fs_roundtrip.py, with additions:
   - nilfs2 and ext3;
   - xattrs over 4 KiB;
   - a hard link changed between backups;
   - a near-full zstd btrfs/bcachefs space check;
   - each guest's mkfs options, from its dump.
6. Judge self-tests with planted differences. A judge change re-judges the kept records.
7. Phase 2 adds partition and format replay, and the loop layer.

**Pass.** No failures. The plan diff is empty or explained. Loop-FS results match expected-coverage.json.

**Cost.** ~25-30 min wall (fs_roundtrip takes 15.6-26 min per host), 0 gm. It writes at most ~1 GB of build cache (est.).

### 2.2 SticTest2

**Purpose.** Show that both archivers keep BackOtter's metadata on every distinct cell.

**Trigger.**
- "SticTest2" runs HostTest2's archiver part, then drive.py on the selection.
- "SticTest2All" runs all 41 checks.
- "SticTest2 GUEST…" runs the named guests.

**Scope.**

| Change | What runs |
|---|---|
| Code that never touches files (chunker, repo format, index) | Tier P: one representative per changed platform binary. Build changes take the oldest: freebsd14, debian12 |
| File-touching code, or one OS's attribute code | Every class of that OS |
| A guest image | That guest, if a cell moved (kernel line, OpenZFS, LSM, mkfs major). Otherwise it is marked stale |
| A shared helper | One guest per use path. For cmp-run.sh: a RAM-disk ZFS guest, a /dev/shm UFS guest, a tmpfs Mint guest, sculpt2610 |

**Dropped:**
- every guest on every change;
- identical-key reruns;
- zfsmirror outside sweeps;
- GuestTest's SticTest column;
- cross-tool directions on guests (decision 4).

**Pass.** Exit 0, and verdicts equal to the class's last pass. A new LIMIT fails until it is classed.

**Cost.**
- Tier P: 6.1 + 11.9 + 6.05 + 5.7 + 7.4 + 6.2 + 0.9 = **44 gm**, ~15 min.
- All classes: **179 gm**, ~45 min (est.).
- SSD writes: ~0 GB against ~14, with aerynos's scratch in RAM.

### 2.3 HoltTest2

**Purpose.** Show that bko-holt plus rustic-bko rebuilds every distinct layout and boot path, and that the result boots.

**Trigger.**
- "HoltTest2" runs HostTest2's holt part, then ht1 on the selection.
- "HoltTest2X" (X = D, A, M, U, Z, F, C, V, H, I, BSD, Obscure) runs HoltTest1X's full list, RAID guests included, ignoring the ledger.
- "HoltTest2All" runs all 45 sources.
- Variants are extra words: +bigger, +oneshot, +degraded, +race.

**Scope.**
- Guests whose golden plan changed.
- Code no plan shows (exec, close, restore, boot stage, other/*/load): the classes in holt.md (c)'s source map.
- Guests whose update changed boot tools. A kernel-only bump marks the guest stale.
- On a rescuer change, the oldest-target representatives. The live freebsd15-ufs, netbsd11 and haiku6 images count as rescuers.
- On a close or exec change, +race on the stacked singletons.

**Steps.**
- Source and backup rerun only if their key (generation, dump-code digest, rustic-bko) changed; otherwise `rebuild verify`.
- Refusals run once per rescuer family and build.
- Restores use the shipped rustic-bko.
- HT_RAM=1 applies when the reservation fits.
- A pass keeps only logs, dumps and compressed fsmeta.
- +race loops mount and close 20 times in ht1-debug.sh's RAM rescuer.

**Dropped:**
- every guest on every change;
- the 146 GiB r3all kept;
- unchanged images' source and backup (64 of r3all's 186 phase-minutes);
- per-guest refusals;
- mx25 outside sweeps.

**Pass.** HoltTest1's verdicts, plus 20 of 20 closes.

**Cost.**
- **One load stage.** Take XFS mkfs: 7 plans change, in 4 sub-cells (partition, LV, LV on RAID5, md1). That is 4 × 2.6 min = **~10 gm**, ~15 min, ~0 GB. The 2.6 min of rebuild and verify comes from r3all's medians: rescuer 39 s, stages ~75 s, verify 39 s.
- **Worst case,** a stage every guest runs: 38 × 3.2 = **~121 gm**, ~60 min. It writes 0 GB in RAM, or ~68 GB if the six big singletons go to disk (57.3 GiB of restored disks × 1.18).
- **HoltTest2All:** 191 + 5 RAID guests × 5.9 = **~220 gm**.

### 2.4 GuestTest2

**Purpose.** Keep each guest healthy, current and identical on every host that holds it (the fleet hosts' full `vms/`, and the cache hosts' links to published versions), and keep the fleet complete.

**Trigger.**
- "GuestTest2" checks the guests whose generation, harness files (vm-update, update/*, vm-run, boot-state.sh, the guest's own options) or upstream stamp changed. Each stream's canary runs first.
- "GuestTest2All" is GuestTest1's graded audit.

**Steps.**
1. Identity from image-state, size, mtime and `qemu-img info -U`, in seconds; sha256 only after merges, or in All.
2. Static checks through the dump or `vm-mount --ro`: security suites (#19), loader defaults, fstab against the layout.
3. `--try` with VM_UPDATE_RAM=1 and no trim, then boot-state.sh, `findmnt --verify` and an fstab-mounted check.
4. A log diff against a per-guest baseline; agents read only new lines.
5. Supported OSes and layouts checked against vms/.

**Dropped:**
- hashing 213 GiB per host;
- `--try` on unchanged streams;
- trim in `--try` (10.3-12.4 GiB zeroed per guest);
- overlays on SSD;
- its own SticTest round (199 gm in run 1);
- ~1.5 h of agents reading logs.

**Pass.** Identity holds. The reboot runs the newest kernel. There are no new failed units or missing mounts, and new lines are triaged.

**Cost.**
- All: 44 × ~1.8 min (run 2's 1.26 + ~0.5 boot-state) = **~79 gm**, ~25 min, ~0 GB against ~8.
- After merges: 44 × 0.75 = **~33 gm**.

### 2.5 ImgTest2 (new)

**Purpose.** Show that a new rescue image restores real machines. HoltTest1 uses its own rescuers.

**Trigger.** "ImgTest2" runs after img-one.sh: the boot tests, then HoltTest2's rebuild and verify using that image as the rescuer.

**Representatives.**
- **Linux:** the oldest target for each thing the image writes:
  - debian12 (ext4 onto 6.1);
  - ubuntu24 (OpenZFS 2.2.2);
  - debian13-stack (md, LUKS and LVM onto 6.12, #13);
  - rocky10-raid5 (XFS on md);
  - nixos2605 (bcachefs);
  - aerynos (f2fs-tools).
- **Other images:** freebsd14, netbsd11, openbsd8, dragonfly6, omnios58, haiku6.

**Pass.** The boot tests and HoltTest2's verdicts.

**Cost.** **~18 gm** for Linux, ~3 for each other image, ~0 GB. No test has yet run a load from a real image (change.md b).

### 2.6 Test2 and Test2All

**"Test2"** runs HostTest2, then whatever SticTest2, HoltTest2 and GuestTest2 select, in one queue across the run's hosts.
- Everything runs as scripts. Agents only triage FAILs and new lines, and are never messaged mid-run.
- The hosts are the rows of `testvm/hosts.json` (testvm/HOSTS.md): chopin and cinqcent are big; copland, cage and later czerny are small. A new host (cage, copland, czerny) joins after HostTest2 and one representative per family pass on it; joining is setting its `default` there, after the provisioning steps in testvm/HOSTS.md.
- A **small host** is one whose ceiling some cell's reservation exceeds: the ceiling is the most one `cmp-run.sh --reserve` can ever get there (cmp-run.sh's `ceiling()`, in t2's host facts; copland's and cage's are 9.0 GiB of RAM disk and 9.2 GiB of memory). A small host joins after the HostTest2 layers and one representative per family pass on it, among the cells that fit it. The cells that don't fit it are placed only on the other hosts, also after it becomes a default host; a cell that fits no host of a run is not runnable there, never queued.
- Placement also goes by each host's capabilities in its facts (`caps`, `guests`): a cell is kept off a host that lacks a guest it boots (a cache host), host OpenZFS for a `holt.needs_host_zfs` source, or HoltTest's rescuer and debs, and the plan names why; with no host left it is not runnable. What stays on the big hosts (unpublished guests, host-ZFS sources, builds, over-ceiling cells, pins) is listed in testvm/HOSTS.md.

**"Test2All"** is the weekly sweep: SticTest2All, HoltTest2All and GuestTest2All. It gives every guest its own pass and catches what rotation missed.
- Cost: 282 + 220 + 79 ≈ **580 gm**, ~2 h overnight (est.).
- SSD: 0-14 GB with RAM first, where the three Test1s wrote 227 GB on 11-06.

## 3. Shared infrastructure (est. work-days)

| # | Item | Days | Needs |
|---|---|---|---|
| 1 | **Input ledger.** One row per test and guest: verdicts and jobs.log bytes. The key: per-platform source digests, image generations, check and helper hashes taken on the host (sync-hosts.sh copies uncommitted files), judge hashes, rescuer sha256, host kernel/ZFS/QEMU/OVMF, need sets. It decides skip, stale and rotation, with `--explain` | 2 | build-round.sh .digest, image-state |
| 2 | **Class table.** Cells from dumps and in-run probes; automatic splits | 1 | 1, 4 |
| 3 | **affected v2.** Maps fork files to platform × FS × file-touching, holt files to guests (holt.md c), and helpers to guests (change.md c) | 1.5 | 2 |
| 4 | **Dump corpus.** 45 redacted dumps (~7.5 MB), golden plans, signatures, and invariants (/ mounted, fstab mounts planned, pool disks in use, IDs as dumped) | 1 | redact.rs, check_plan |
| 5 | HostTest2 runner; keeps each representative's fsmeta records for re-judging | 2.5 | 4 |
| 6 | Partition and format replay on sparse files | 2-3 | 4 |
| 7 | Loop-device layer with a close loop (layload.md:536-541) | 2-3 | ht1-debug.sh |
| 8 | ht1: delete on pass, HT_RAM, backup cache, per-family refusals, shipped rustic-bko, variant words | 1.5 | 1 |
| 9 | The image as rescuer: an SSH key or the serial console, plus 9p | 1-2 | images that carry bko-holt |
| 10 | GuestTest2: RAM `--try` without trim (hours), no merge of no-op updates, boot-state, baselines, upstream probe | 2 | 1 |
| 11 | Need-set parity in A4-A6, B2 and B3, and between the host layer and A1 | 1 | 2 |
| 12 | wear-report.py tags by test; zfsmirror and aerynos scratch go to RAM | 0.5 | jobs.log |

Phase 1 is items 1-5, 8, 10 and 12: about 12 days (est.). The rest takes 6-9 more.

## 4. Scenarios

Each cell gives gm / wall / SSD GB. Test1's figures are measured or summed from measured parts; Test2's are estimates.

| Scenario | Test1 | Test2 |
|---|---|---|
| (i) Nothing changed | Full reruns: 510 / 1 h 47 / 227 (11-06) | 0 / 1 min / 0 |
| (ii) Fork change in one OS's attribute code | FreeBSD: platform regression 69 + final round 282 = 351 / ~85 min / ~14. Linux: 180 + 282 = 462 | FreeBSD: freebsd14 + B2 + B3 = 31 / ~20 min / 0. Linux: 122 / ~45 min / 0 |
| (iii) OS-independent fork change | Full round: 282 / 37-65 min / ~14 | Code that never touches files: 44 / ~30 min / 0. Restorer: 179 / ~50 min / 0 |
| (iv) bko-holt change in one load stage | HoltTest1All: 191 / 90 min / ~200 | ~10 / ~15 min / 0. Worst case: 121 / 60 min / 0-68 |
| (v) Fleet-wide guest OS updates | All three: 510 / 1 h 47 / 227 | GuestTest2 after merges 33 + ~6 Stic classes 35 + ~8 Holt boot-tool guests 40 = ~110 / ~45 min / 0. The round itself still writes 50-85 GB |
| (vi) New guest or layout | raid1: 53 / 26 min / 40, then regressions | Per guest ~30 (Stic 6, Holt with variants and +race 20, Guest 3) / ~25 min / 0. Deliberately more |
| (vii) Harness or judge change | Judge rules were followed by a whole r3all (191 / 90 / ~200); 39c9416 by st2a (201) | Judge change: 0 (self-tests, re-judging). Helper: ~25. One check: ~5 |
| (viii) New rescue image | Boot tests only; never a load | ImgTest2: 18 / ~15 min / 0 |

## 5. Coverage replay

| # | Test2 catcher | When, vs Test1 | gm |
|---|---|---|---|
| 1 | Binary check (static glibc with NSS), else tier P's Linux rep | At build: earlier, 1 guest not 4 | 0-6 |
| 2, 3, 4, 8 | HostTest2 loop-FS: xattrs over 4 KiB, cross-tool node compare, changed hard link, vfat/xfs with joined need sets | At build: earlier (Test1: r14-r18) | 0 |
| 5 | A3, A6 (fedora44-uefi), tumbleweed | First run: same. Rotating A6 to fedora44 would miss it; §2.0 forbids that | ~10 |
| 6, 7, 9 | Singletons ubuntu24, omnios58 (in tier P), nixos2605. #9 also via loop-FS bcachefs at 512 B | First run: same | 16, 6, 4 |
| 10 | Symbol check; tier P takes freebsd14 | At build: earlier. Test1 needed a new guest | 0 |
| 11 | Loop-FS space check, else omarchy in the sweep | At build, or up to 7 days later | 0 or 9 |
| 12, 16 | Plan invariants on the dumps | At the first dump: earlier or same | 0 |
| 13 | Rescuer rule: debian13-stack, the oldest md target | First run after a rescuer change: same | ~3 |
| 14 | +race on a close change; loop layer | At the change: earlier than Test1's third sweep | ~10 |
| 15 | Format replay (XFS geometry) and loop layer (mdadm offset), then the new guests | At the first dump: same, cheaper | ~12 |
| 17 | HoltTest2 on each new OS, with development loops in RAM | Same, with far fewer bytes (Cinqcent ~465 GB on 11-05) | loops |
| 18 | `--try` on its singleton stream, plus boot-state's uname | First try after the update: same | ~2 |
| 19 | Static security-suite check | At image build: earlier | 0 |
| 20 | fstab-mounted check | First GuestTest2 (11-05): earlier than run 2 | ~1 |

**Later or missed:**
- #11, if the space check is never built: the sweep catches it, up to 7 days late.
- A defect tied to a trait judged irrelevant (ghostbsd26-zfs's dataset properties, HBSD's hardening): caught up to one rotation cycle (≤3 class runs) or one sweep late.

## 6. Risks and what is lost

- **Hidden cells** (say, a patch-level kernel regression) make a wrong class. Drift detection (recorded cells, auto-split), need-set parity, rotation, fan-out and the weekly sweep bound the damage.
- **Unrecorded inputs** (mirror contents, microcode, timing) are left to the sweep.
- **Fewer repeats** surface races and flakes later. +race and the sweep help.
- **The backup cache** means a backup-side rustic-bko regression shows only in SticTest2 or the sweep.
- **Silent skips from ledger bugs:** every skip gets an `--explain` line, and two shadow weeks compare Test2's picks with a weekly Test2All.
- **Lost:**
  - each Linux guest's own pass every round (now weekly);
  - cross-tool restores on guests between sweeps;
  - the free count of how many guests a defect hits (fan-out restores it on failures).

## 7. Decisions for the user

1. **"Every Linux guest gets its own pass" (2026-11-01).** Change it to: every guest at least weekly, and whenever its own cells change, with one representative per class in between. zfsmirror, rocky10-lvm, freebsd15-geli (Stic) and mx25 (Holt) become sweep-only. *Recommend yes.*
2. **Disk or RAM.** On 11-04 the user chose disk thrash for speed. Since 11-06 Chopin's /bkoscratch is the P3 itself (QLC, 6% worn, ~0.51 TB/day), so work-in-ram.md's "never the P3" is stale. *Recommend:*
   - RAM first wherever it fits;
   - the big HoltTest guests one at a time per host in sweeps;
   - ht1.sh deleting on pass;
   - no compare.py `!` rounds (they wrote 239 GB for 10 guests on 11-04).
3. **Sweep cadence.** *Recommend weekly Test2All, run overnight.* Move to every two weeks after a quiet month.
4. **Cross-tool directions** are a courtesy, and take ~58% of freebsd15's check. *Recommend* all four in HostTest2 and Test2All, and same-tool only in triggered guest runs.
5. **Build order.** *Recommend Phase 1 (~12 days, est.) and two shadow weeks before Test2 replaces Test1 for routine use.* The Test1 names stay for explicit full runs.
