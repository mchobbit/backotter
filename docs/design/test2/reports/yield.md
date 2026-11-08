# What SticTest1, HoltTest1 and GuestTest1 found (evidence, 2026-10-30 to 11-07)

**Sources** (short names used below):
- **Memory notes:** `/Users/mchobbit/.claude/projects/-Users-mchobbit-git-backotter/memory/` (`stictest1.md`, `holttest1d.md`, `guesttest1.md`).
- **Round reports from the archiver session** (in `/private/tmp/claude-501/-Users-mchobbit-git-backotter/b43d6ef3-8bbc-4dc4-9768-d915a92c69e4/scratchpad/`):
  - R23 = `b2/STICTEST1-R23-REPORT.md`
  - ST1R = `st1round/REPORT.md`
  - ST2R = `st2round/REPORT.md`
  - REL1 = `rel1round/REPORT.md`
  - REL4 = `rel4st1/REPORT.md`
  - FR = `b2/FORK-REGRESSIONS.md`
- **GuestTest1 second-run report:** GT2 = `/private/tmp/claude-501/-Users-mchobbit-git-backotter/39d24434-6f21-44ae-8f2e-b828b29e8fb2/scratchpad/gt2/REPORT.md`.
- **Host files** (chopin and cinqcent):
  - SticTest1: `~/backotter-work/stictest1/GUEST/{run-*.out,logs/MARK/}` and the drivers' `progress*` files.
  - HoltTest1: `/bkoscratch/holttest1d/RUN/GUEST/report.txt` and `queue/RUN/runner-HOST.log`.
  - GuestTest1: `/bkoscratch/gt1/try-*.log`.
  - Wear log: `/bkoscratch/wear/jobs.log`.
- **Commit hashes** are in backotter, restic (r:), rustic_core (rc:) or rustic (ru:).
- **My parsed extracts:** `/private/tmp/claude-501/-Users-mchobbit-git-backotter/6e7b60cc-f873-4450-892c-1b053e7a5606/scratchpad/t2/yield/`. They are `ht1-reports.txt` (all 363 HoltTest1 reports), `st1-marks.txt`, `st1-runouts.txt`, `st1-runout-fails.txt`, the runner logs and the `p-*.txt` driver progress files.

## (a) Findings

| # | Finding | Family / run | Guest(s) | Step | Class | Fix | Cheapest check that finds it |
|---|---|---|---|---|---|---|---|
| 1 | Static-glibc rustic-bko crashes with SIGFPE (exit 136) on owners with no name | ST1 r14/r15, 11-02 | mint22, pop24, zorin18, fedora44-uefi, all at once | backup exits 0 | rustic-bko build | ru:79bf2fc, 9254d98 | one-guest smoke run |
| 2 | Xattrs over 4 KiB backed up empty (xattr 1.6 on rustix 1.1; stock rustic 0.11.4 ships both) | ST1 r15–r17 | rocky10 (60 kB, XFS), tumbleweed (15 kB, btrfs), fixtures on debian13 and fedora44-uefi | user.* xattrs | rustic-bko, likely inherited; data loss | rc:44e1b03 | host loop-FS or unit test |
| 3 | Node parity: device 0, mode 0, placeholder ancestor dirs; device IDs | ST1 r14/r15 | 10 guests in one pass | snapshots node-identical | parity, both tools | rc:be96479, 44e1b03, 39b4368; r:9dcde8697 | host loop-FS cross-tool compare |
| 4 | Restore over a changed hard-linked file leaves NULs or an empty file (stock restic has it too) | ST1 r14–r18 | zorin18, mint22, mx25, pop24 (same check template) | restore over changed tree | restic-bko, inherited | r:8e3869cce | unit test |
| 5 | Restore exits 1 when removing security.selinux | ST1 r15 | fedora44-uefi, rocky10, tumbleweed | restores exit 0 | both tools | r:8a2a5379d, rc:44e1b03 | first SELinux guest |
| 6 | FS_IOC_FSSETXATTR leaves append-only set on OpenZFS 2.2 | ST1 r15 | ubuntu24 | restore over immutable | both tools | same | first guest of that OpenZFS (hosts run 2.3.9) |
| 7 | illumos: doors made backups exit 3; tmpfs crtime/ACL refusals made restores exit 1 | ST1 r15–r17 | omnios58 | exits 0, tmpfs restore | mostly restic-bko | r:8a2a5379d, 8e3869cce | the only illumos guest |
| 8 | FAT attributes and XFS per-file options kept by neither tool | ST1 r15 | fedora44-uefi (`logs/run2-r15.out`) | inventory needs | feature gap, both | r:2613ddfcf, rc:393cf55 | host loop-FS (vfat, xfs) |
| 9 | bcachefs: casefold ENOTEMPTY gives exit 1; holes under 4 KiB lost at block_size 512 | ST1 r14→r22, fixed r23 | nixos2605 | exits 0, sparse | both tools | r:8c5ff1bed, rc:eea4762 | first bcachefs guest (the host's mkfs picks 4 KiB, e5fce9f) |
| 10 | rustic-bko built on FreeBSD 15 won't start on 14 (setgroups@FBSD_1.8) | GT1 gt1-fix89 | freebsd14 (new guest) | every need | rustic-bko build | 22a5089 | `--version` on each OS release |
| 11 | Restore preallocated every file, so a compressed btrfs ran out of space (13.4 GiB into 14 GiB) | HT1 r4l, 11-05 | omarchy | load restore | both archivers | rc:ed28fe0, r:b74535444 | host loop test: near-full zstd btrfs. Review had flagged it on 11-03 (FR B10) |
| 12 | Plan bugs: noauto ZFS root never mounted, archiver path not found, ZFS disks not "in use", MBR disk ID read as decimal | HT1D first dry run | debian13, debian13-zfs | plan/status | holt | 4d5e9e7 | unit test on a real dump (the sample used 0x, so no test caught it) |
| 13 | md logical_block_size written by the rescuer's newer kernel, which Linux 6.12 refuses, so the VG is missing at boot (my inference) | HT1 fin3d | debian13-stack | restored boots alone | holt | 4c28b2e (md-lbs) | first md guest with a newer rescuer |
| 14 | close races udev: LV "in use", zpool "busy" | HT1 r2all; earlier runs had passed | debian13-stack, ubuntu26-zfs | load close | holt (race) | 1626714, 2e6c1b5 | repeat close in a loop (ht1-debug.sh) |
| 15 | mdadm refuses `--data-offset=0K` (metadata 1.0); `mkfs.xfs` with agcount alone makes another geometry | HT1 raid1, 11-07 | fedora44-raid, rocky10-raid5 (new) | assemble; dump check | holt | 78018db | host md/LV loop test |
| 16 | Legacy-mounted bootfs: nothing mounts / | HT1 raid1 | omnios58-mirror (new) | plan | holt | 3671e96 | plan-only test on its dump |
| 17 | Bring-up gaps for each OS (OS gates, f2fs/nilfs2/JFS, grub2, musl, BSD/illumos/Haiku) | HT1 r1a–r31: 149 runs, 52 PASS | each new OS | all stages | holt development | 3b74f70, 4c28b2e, a0c5852, 38393b8, ae2ffd3 | first guest of each OS |
| 18 | pop24 boots its old kernel, and an update's autoremove bricks it | GT1 first run, `--try` | pop24 | update | guest | dd0db8e | one `--try` |
| 19 | devuan6 has no -security or -updates suites | GT1 first run | devuan6 | update | guest | dd0db8e | static apt-source check |
| 20 | ESP hidden under bpool's /boot (no zfs-list.cache) | GT1 second run; HT1 r1all saw dracut fail on /boot/efi | ubuntu26-zfs | `--try` logs | guest | 3e9c8ca | `findmnt` in one snapshot boot |
| 21 | Harness defects (~25) | all three families | many | – | harness | – | first run after the harness change |
| 22 | Infrastructure | – | – | – | infra | ae478c3, 5f1ce45, ab6c75c | – |
| 23 | Known limits, recorded rather than fixed | – | – | – | OS limit | bf5fe6f | – |

Row 21's main items:
- sockets compared;
- openbsd8's live /dev mtime;
- reserve sizes, RAM_DIR and slots (bd2a178, 184a5d9, 39c9416);
- the netbsd11 reset (a1abe96);
- btrfs's own NOCOMPRESS flag (2f5b4f8);
- judge masks (4218f97, 37e0f63, 72858ed, 138097c);
- vm-mount importing only one pool;
- no trim zero-fill (04ab019).

Row 22's main items:
- r32 stopped when Chopin's /bkoscratch filled ("No space left on device", `chopin:ht1/r32/tumbleweed/logs/backup.log`);
- admission waits: devuan6 waited ~75 min, cachyos was killed at the 45-min cap, alpinebsd hit its 900 s timeout (R23 §2/§4);
- m lost the kvm group after 5d1b66f, fixed in ab6c75c;
- raid3 ran into "vm-run: unknown option --disk-serials".

Row 23's limits:
- the rebuilt ubuntu24 image is unpatched 24.04, whose ZFS stores every atime as 0;
- an F2FS superblock records the rescuer's f2fs-tools version;
- XFS sb_icount;
- AlpineBSD's kernel package;
- MX's umountfs;
- s6-rc (`guesttest1.md`).

## (b) Runs that found nothing new

| Run | When | Scope | Approx. cost | Why it ran | Result |
|---|---|---|---|---|---|
| ST1 u2 + ab | 11-04 07:09–08:38 | 31 + 10 | ~400 guest-min (`p-u2-*.txt`) | regression after vm-update rounds 1–2, on round-23 binaries | nothing from the updates. The 10 FAILs were old binaries checked against the new A3 rule (`rerun-u2/drive.sh` header); 1 harness bug (a1abe96) |
| ST1 st1a–d | 11-04, 3 h 49 | 31 ×2 + rechecks | ~10 guest-h | the user's SticTest1 on unchanged forks | 0 tool; 4 low harness/infra (ST1R §1) |
| ST1 st2 | 11-04, 1 h 10 | 31 | ~210 guest-min | speed-up validation | 0 tool; 1 flake (ST2R) |
| ST1 rel1 | 11-05, 52 min | 31 | ~210 | release-profile binaries for the images | 0 tool; omarchy's btrfs flag (REL1) |
| ST1 rel2 | 11-05 07:43–08:20 | 32 | ~200 | rustic +backotter.4 (upstream fixes) | nothing |
| GT1 gt1-rel2 | 11-05 11:02–11:38 | 31 | ~200 | GuestTest1's backup/restore column | nothing; same binaries as rel2 three hours earlier |
| ST1 rel3 | 11-06 08:03–09:08 | 14, Cinqcent only | ~130 | preallocation rule (.4/.5) | nothing new; 3 runs left no exit file |
| ST1 rel4 (+t, g, n) | 11-06 22:53 to 11-07 02:36 | 39 + 5 | ~310 | upstream picks .5/.6; changed guests; 2 new checks | nothing (REL4) |
| HT1 r3d, r1m, r3a | 11-05 evening | 18 runs | ~1.3 guest-h | confirmation after fixes | "no bko-holt bug found" (`holttest1d.md`) |
| HT1 r32 | 11-06 07:07 | 35 started | stopped | confirming full run | disk full only |
| HT1 r3all | 11-06 21:53–23:23 | 40 | ~190 guest-min, ~150 GB kept (94 GB Chopin + 55 GB Cinqcent by du) | confirmation on one committed bko-holt | nothing, 40/40 |
| GT1 second run | 11-06/07, ~1 h 50 | 39 `--try` | reused rel4 for its column | weekly audit | guest defects only (#20, trim) |

Two cost markers:
- One HoltTest1 BSD-style run wrote 4.26 GB in 305 s (`cinqcent:/bkoscratch/wear/jobs.log`, omnios58-mirror raid4).
- GuestTest1's first-run `--try` overlays totalled about 19 GiB of apparent size over 32 guests (`/bkoscratch/gt1/try-*.log`).

## (c) Hit rates

**Duplicates found the same thing at the same moment.** #3 showed up on 10 guests, #1 on 4, #4 on 4, and u2's A3 FAIL on 10. One guest per class would have found each of them.

**Later guests found new things only when they brought something new.** That was a version, a layout or a fixture case:
- #6: ubuntu24's OpenZFS 2.2;
- #10: freebsd14's libc;
- #13–#16: the RAID, mirror and stack guests;
- #9: bcachefs with 512-byte blocks;
- #5: SELinux fired on fedora44-uefi, rocky10 and tumbleweed, but never on fedora44. Which fixture case a check carries decided it.

No second guest of the same OS, file system and version ever found more.

**SticTest1 after each guest's first pass:**
- It found no archiver bug on any guest after that guest's first full pass (timelines in `st1-marks.txt` and `st1-runouts.txt`).
- 25 of 41 guests never failed again.
- The other failures were harness bugs, admission waits, flakes, u2's binary skew and ubuntu24's rebuilt image.
- The 10 slowest guests take 48% of SticTest1's time (`testvm/stictest1/README.md:125-133`). None of them found anything after its first pass.

**HoltTest1 after each guest's first pass:**
- Real holt bugs came only from debian13-stack and ubuntu26-zfs (#14), plus #13.
- Judge changes came after OS updates (freebsd14's boot-environment clone, tumbleweed's /boot labels in r2all) and from nixos2605's UUID ordering in r4u.
- 29 of 40 guests never failed again, apart from r32's full disk.
- A measured run took 4.8 min on average in r3all (dragonfly6 longest, at 15.6 min). The README estimates 15–25 min (`testvm/holttest1d/README.md:269`).

**Flaky guests:**
- freebsd15-ufs and ghostbsd26: the rustic→restic direction ends with no verdict (3 of about 47 direction logs). A vm_put failure is hidden by `ssh -q` (ST2R).
- haiku6: a boot didn't come up (r22).
- ghostbsd26-zfs: ssh exit 255 right after boot, 1 in 8 (5918ec2).
- openbsd8: the serial console moves /dev/tty00's mtime.
- omarchy: btrfs sets NOCOMPRESS on its own.
- ZFS: st_blocks varies between restores (a09fdd6).
- devuan6, cachyos, alpinebsd: waiting for room on the host.
- debian13-stack and ubuntu26-zfs: udev races, which were real bugs (#14). debian13-stack also clashes with another copy's LVM VG name (r2d).
- omnios58: failed 16 times before its first pass.

## (d) Where the bugs came from

**Archiver bugs: 13.**
- 11 of them in SticTest1's first day (11-02, rounds r14–r23), right after the forks' feature batches of 10-30 to 11-01.
- 2 inherited from upstream (#2, #4), 1 build setting (#1), 3 parity (#3 and the related device-ID items), 1 feature gap (#8), 4 specific to an OS, file system or version (#5, #6, #7, #9).
- After 11-02: one from a new guest (#10) and one from a whole-machine restore (#11).
- Fork changes after 11-03 produced 0 SticTest1 findings in about 38 guest-hours. Those changes were the fresh-target rules, upstream picks .4 to .6, and the preallocation rule. The fresh-target defects were found by code review (FR), not by any test.

**Earlier rounds (10-30 to 11-01)** found the first wave:
- host loop-FS tests (fs_roundtrip.py);
- each OS's `roundtrip.sh`;
- compare.py whole-system restores.

Examples: r:a0b876723, eb691e288, ab475a480, 6d21bd8ad; rc:ccc68a2.

**Holt bugs** came almost entirely from bringing up new OSes (#17). After bring-up they came from new layouts (#15, #16), from repetition (#14) and from a newer rescuer kernel (#13).

**Harness changes** gave the most FAIL lines. They also broke things on their own:
- 5d1b66f dropped m's kvm group (fixed in ab6c75c);
- 9a9f602 broke every native bko-holt build (fixed in a69b711);
- u2's checks ran ahead of its binaries.

**OS updates** produced no archiver or holt bug. They found guest defects (#18) and caused judge updates. Image rebuilds caused the ubuntu24 limit, openbsd8's /dev noise, and build images left with stale SSH keys (494e218).

**Levers this suggests:**
- Pick tests by what changed:
  - fork change: host loop-FS tests plus one guest per unique OS/FS/version;
  - harness change: only the guests touched;
  - new guest or layout: a full run on that guest alone.
- Never rerun identical binaries.
- Add to the host loop-FS tests: a xattr over 4 KiB, a hard link changed between backups, and a near-full zstd btrfs.
- For holt:
  - run plan-only tests on the 40 collected dumps (`holt/tests/redact_dumps.rs`, BKO_HOLT_DUMPS);
  - loop the close stage to catch races;
  - keep only logs from runs that pass.

## Gaps in this evidence

- I found no disk-write figures for each job before 11-07 04:49: `jobs.log` starts then. SticTest1 costs are guest-minutes, not bytes.
- No logs remain for st1a–st1d by guest beyond `check.out`, and r14–r19 have no driver files. I estimated those rounds' costs from the round reports.
- HoltTest1's per-run durations come from the start time in each report header and the report's mtime. They are lower bounds for runs that were resumed.
- #13's cause is my inference from timing and 4c28b2e's message. #2's origin, inherited or brought in by the rustix bump (ru:215ebd3), is not established.
- I found no stored passing report for fedora44-raid or omnios58-mirror. Their passes rest on re-judging after a fix, and on the README.
- Nothing records why rel3's 3 runs were cut short.
- I didn't read the compare.py rounds (u2c, u3) as a family of their own.