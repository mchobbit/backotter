# Test layers below whole-guest runs: inventory and gaps (researcher "cheap")

I only read files, logs and git history; I ran no tests. `chopin:` and `cinqcent:` mark paths on the hosts. "est." marks an estimate.

## (a) Layer table

| Layer | What it checks | Runtime | Disk writes | VM? | How often it runs today |
|---|---|---|---|---|---|
| holt unit tests (`holt/src`, 218 `#[test]`) | **Parsing:** 64 tests in 17 probe modules, plus 70 in parttable, json, bytes, efi, bootcode, mounts, osrel, sys, blockdev and mdsb. **Report and boot:** 22. **Load planning:** 31 (commands.rs 14, mod 6, target 3, plan/status/layout 2 each). **write-table:** 2, on synthetic GPT/MBR in memory. **Non-Linux parsers:** 12. **Redaction and bug report:** 10. **scenarios.rs:** 9 fake Debian 13 machines built with testutil.rs's `Tree` (testutil.rs has no tests of its own). | 0.3 s of test time (chopin:/bkoscratch/tmp/holt-test-2e6c1b5.log), plus the build | build directory only | no (Debian 13 host) | Only by hand: 3 saved logs, all 2026-11-06. No script or CI runs it: build-holt.sh:86 and ht1-debug.sh:114 only run `cargo build`. |
| holt integration tests (`holt/tests`, 25) | **realtools (13):** images made by mkfs, sfdisk, sgdisk and cryptsetup, checked against blkid, dumpe2fs and xfs_db; no root needed. **cli (5):** includes the live host against lsblk. **load_cli (4).** **redact_dumps (1):** plans every dump but checks only that redaction leaves the plan unchanged. **root_stack (2, `#[ignore]`):** md + LUKS2 + LVM and a ZFS mirror; needs root and "a throwaway guest" (root_stack.rs:1-10). | realtools 2.2-3.6 s | sparse image files | root_stack needs one guest | redact_dumps ran once, over r3all's 40 dumps (commit b2f2535). I found no record of a root_stack run. |
| Fork test suites (testvm/audit: `fork-tests.sh` against `stock-baseline.sh`) | restic fork: 1873 pass / 48 skip, against stock's 1701 / 33. rustic_core: 489 against 303. Plus 11 GOOS cross builds. It runs as user m, so 15 root-only restic tests skip (audit README:106-108). | 97 s warm; 8.8 min for new trees (README:89-90) | build caches on /bkoscratch: Go 6.8 GB, cargo targets 5.8 GB (du) | no | Last runs: 2026-11-04 (restic d2112d6) and 2026-11-05 (rustic_core 1cd8fd5), per the hosts' bko-audit/fork/. Since then restic has 8 new commits and rustic_core 7, none run through it. |
| fs_roundtrip.py with torture.sh and fsmeta.py | Host loop filesystems: ext4, xfs, btrfs, f2fs, jfs, ext2, vfat, bcachefs (Chopin only, via arch-root), tmpfs, zfs and zfs-ds (fs_roundtrip.py:85-99). Per filesystem: all 4 directions, a restore over a changed tree, a rerun, node identity, st_blocks, sockets, and the SPEC3 2 snapshot identity check. | about 5 min per filesystem alone; 8 at once 26 min, 3 at once 15.6 min (split-chopin-cinqcent.md:23-25) | about 0: images and repositories live in /mnt/bko-ram | no (root) | Last run on the host itself 2026-11-01 (chopin:~/backotter-work/round10/logs/linux). Since then it runs only inside the SticTest1 checks of ZFS guests (stictest1/ubuntu24/check.sh:91). |
| sparse-rt.sh and nonroot-rt.sh linux | Hole layouts in 4 directions with the st_blocks rules; restores as uid 4321 onto ext4 and ZFS | sparse-rt about 10-20 min per filesystem at BIG=20 (from round10 file times; est.) | RAM | no | 2026-11-01 on the host (round10 sparse/, nonroot.exit=0) |
| run-all.sh (with verdict.py, expected-coverage.json, affected.sh) | Runs the rows above plus per-OS guest round trips | 34-62 min per host (round 6, per memory) | mostly RAM | partly | Last used 2026-11-01 (round10; cinqcent:~/backotter-work/loop/r12) |
| run-guest-rt.sh / OS/roundtrip.sh, zfs-guest-rt.sh | Round trips on BSD, illumos and ZFS-root guests | about 8-26 min per guest (est.) | RAM (`--ram` overlays) | one OS | OS/roundtrip.sh runs inside every SticTest1 BSD/illumos check (freebsd15/check.sh:43) |
| compare.py | Whole disk in 4 directions onto Python `recreate()` layouts, with 5 boots | 4-37 min per guest, mostly 4-8 (compare/*/results.json) | With `!`, restored disks go to /bkoscratch: about 4 × the source size (2.4-12.5 GB), roughly 0.5 TB for 25 Linux guests (est.) | yes | last batch 2026-11-04/05 |
| drive.py `--self-test` | the SticTest1 queue logic | seconds | none | no | by hand |
| fsmeta.py, holttest1d/judge.py, verdict.py, the per-guest judge.py files | They have no tests of their own. | | | | |
| CI | BackOtter's `.github/` holds only issue forms. The forks' main test workflows run only on master/main pushes or on PRs (restic tests.yml, rustic ci.yml, rustic_core ci.yml), not on branch backotter. A few workflows have push triggers with no branch filter (rustic_core coverage.yaml for `**.rs`, and the audit workflows); whether Actions are enabled on the forks is unchecked. | | | | effectively none |

**Whole-guest runs, for comparison:**
- **SticTest1:** about 5.0 guest-hours by the README's estimates (stictest1/README.md:125), 38 min wall per full pass (round-speedups.md:29), and about 8 full rounds between 2026-11-04 and 11-06 (the st1-* log marks). 39 of the 41 checks keep guest writes in RAM; only aerynos (its overlay) and ubuntu26-zfsmirror (a 24 G spare disk) write to /bkoscratch.
- **HoltTest1All, run r3all:** 1.5 h wall. Logged phases took 139-923 s per guest; the README (line 269) says a run takes 15-25 min. It kept 94.3 + 55.5 GB on /bkoscratch (du). There are about 117 HoltTest1 run directories across both hosts from 2026-11-05 to 11-07.
- **Wear log:** it only started on 2026-11-07. Between 04:13 and 09:00 UTC, Cinqcent's SN770 wrote 16.9 GB (smart.log). 16.6 GB of that came from six `ht1-bsd.sh omnios58` debug runs at 0.5-4.7 GB each (jobs.log). Chopin's P3 wrote 12.8 GB, almost none of it attributed to a job.

## (b) Tested only inside guests today, but testable lower

1. **Linux archiver metadata.** Both Linux binaries are static: restic is built with CGO_ENABLED=0 (~/git/restic/build-backotter.sh:51-53) and rustic for musl (~/git/rustic/build-backotter.sh:31-36). So the 27 Linux SticTest1 guests (about 3.0 guest-hours) all rerun the same binary pair.
   - What actually changes from guest to guest is the kernel, the guest's own files, and SELinux. Kernels from the r3all dumps: 6.1 ×1, 6.8 ×1, 6.12 ×7, 6.18 ×3, 7.0 ×5, 7.2 ×8, 7.3 ×2.
   - Both hosts run 6.12.115+deb13.1, the exact kernel of debian13, debian13-stack, debian13-zfs, devuan6 and mx25. Their SticTest1 checks total about 48 min by the estimates.
   - fs_roundtrip.py has no nilfs2 (zorin18's root) and no ext3 (devuan6), although mkfs.nilfs2 is on both hosts. The guests' own files could be read from their images with vm-mount, without booting them.
2. **Restored space on compressed btrfs/bcachefs.** rustic-bko's preallocation wrote files uncompressed. Only Omarchy's whole-machine restore found it, by running out of space (holttest1d.md:103).
3. **load's planner.** `check_plan` (bugreport.rs:465-521) already plans all 40 r3all dumps offline: 7.5 MB in total, 0.7 MB for the Linux ones. But it only checks that redaction doesn't change the plan; no expected plan is pinned.
4. **Partition stage.** Linux layouts get an sfdisk script (commands.rs:291-372). The other systems get write-table, whose `plan(d, size)` is a pure function and can write into any file (writetable.rs:203-235). Untested code: other/load.rs (525 lines), offline.rs (110) and load/exec.rs (138) have no tests at all.
5. **Format stage.** rocky10-raid5's XFS geometry defect (holttest1d/README.md:53-58) was found by a 3-disk guest. Running mkfs.xfs on an image file of the LV's size would show it. The fix (78018db) added only a test of the command text.
6. **Assemble stage.** fedora44-raid's `mdadm --data-offset=0K` refusal (README:46-49) needed a RAID guest. docs/design/layload.md:536-541 plans exactly this layer: rebuild "with load on fresh loop devices, dumps again". It doesn't exist; root_stack.rs never calls load.
7. **Offline dumps.** `dump --disk` opens any file (other/offline.rs:21-25), yet ht1-offline.sh boots a Linux rescuer VM just to run it on Redox and Sculpt disks.
8. **Re-judging.** r3all keeps the source and restored dumps and the fsmeta records (12-155 MB each). Changes to judge.py or fsmeta.py could be replayed against them without rerunning any guest.
9. **ABI.** freebsd14 failed SticTest1 because the FreeBSD rustic-bko needed `setgroups@FBSD_1.8` (guesttest1.md:19). `readelf` on the host would show this.
10. **OS-gated fork tests.** Some fork tests compile only for their own OS, and no script ever runs them:
    - rustic_core: haiku.rs (5 tests), illumos.rs (1), freebsd_netbsd.rs (2);
    - restic: attrs_{bsd,freebsd,illumos}, birthtime_illumos, project_freebsd and quarantine_illumos (6);
    - the native builds only run `cargo build` (stick/build-rustic-native.sh:184);
    - rustic_core's FreeBSD, NetBSD, OpenBSD and DragonFly glue has no tests, and illumos.rs has 1 test in 1043 lines;
    - commit messages mention hand tests on OmniOS r151058.

## (c) What genuinely needs a guest

- **Other kernels.** 13 non-Linux SticTest1 guests (about 2.0 guest-hours) and 13 HoltTest1 guests. This covers:
  - the syscalls for file flags, ACLs, extattrs, illumos sysattrs and BFS attributes;
  - holt's native dump and load (GEOM/gpart, disklabel(64), HAMMER2, illumos zpool, installboot);
  - same-OS rescuers (OpenBSD's DUID, DragonFly's volume ID).

  Unit tests need only one boot per kernel family: 7 of these guests are FreeBSD-based.
- **Linux kernels other than 6.12, and OpenZFS 2.4.** The hosts have OpenZFS 2.3.9, so zfsmirror's pool imports read-only only.
- **Boot chains, initramfs tools and init systems:**
  - Debian/Ubuntu: update-initramfs and update-grub;
  - Fedora/Rocky/Tumbleweed/Void: dracut, grub2-mkconfig and BLS entries;
  - Arch, Artix, CachyOS: mkinitcpio; Alpine: mkinitfs;
  - AerynOS: its prebuilt initrd and blsforme;
  - Pop: kernelstub; NixOS: its generations;
  - Ubuntu UEFI: shim and Secure Boot; and NVRAM boot entries;
  - boot-state under systemd, OpenRC, runit, dinit, s6 and sysv;
  - passphrase prompts: serial console, the ZFS keystore, Plymouth.
- **An LSM with a real policy.** SELinux is enforcing on fedora44, fedora44-uefi, rocky10, rocky10-lvm and tumbleweed. Their fixtures set types and MLS ranges with chcon (fedora44/fixture.sh:93-99), and the restored system needs relabelling. The AppArmor guests record a LIMIT instead, since AppArmor keeps no labels on disk.
- **Booting the result:** firmware mode, GRUB's core image in the MBR gap, ZFS import and md assembly at boot, degraded arrays.
- **Installer quirks** such as Anaconda's XFS geometry or Subiquity's missing zfs-list.cache. These need a guest once; after that the dump captures them and they can be replayed offline.
- **The guests' own health** (GuestTest1). Its SticTest1 column duplicates SticTest1: the second run reused rel4's results (guesttest1.md:23).

## (d) Missing lower-layer tests: cost and what each replaces

| # | Test | Build cost (est.) | Run cost | Replaces or narrows |
|---|---|---|---|---|
| A | Golden plans for the dump corpus: the r3all and RAID-guest dumps, redacted by redact.rs (which already proves redaction keeps the plan) so they can be committed as fixtures. Run with holt's tests before every HoltTest1 queue. | 0.5 day | seconds, no writes | Today a planner change means rerunning HoltTest1 to see what it touches. The plan diff would name the guests a HoltTest2 must rerun. |
| B | Partition replay: each disk's sfdisk script or write-table plan applied to a sparse file (also 1 GiB larger), read back by parttable and `sfdisk --json` | 1 day | under 1 min, a few KB in tmpfs, no root | The partition stage of about 40 guest runs; the first real-layout tests for write-table and other/load.rs |
| C | Format replay: each filesystem's mkfs command applied to an image of its size, probed, and compared using judge.py's dump rules. Optionally also with other distros' mkfs (the BKO_MKFS_BCACHEFS chroot pattern). | 1-2 days | minutes, metadata only, in tmpfs (ZFS needs root) | Defects of the rocky10-raid5 kind, found today only by whole guests |
| D | The layer layload.md plans: root_stack.rs plus `load --commit --through mount` onto fresh loop devices, then dump again and judge | 2-3 days | about 1-2 min per layout (est.; ht1-debug's whole rebuild is 38-91 s, rescuer included) | HoltTest1's rebuild phase for layout-only changes (the fedora44-raid kind). LVM and md names are global to the host (README:193-196), so it runs in one throwaway guest or with prefixed names. |
| E | Put the host loop-filesystem layer back into rounds (run-all.sh's linux and sparse steps), adding nilfs2, ext3, a space check on compressed btrfs/bcachefs, and the guests' own files read with vm-mount | a few hours | 15-26 min wall per host, about 0 SSD writes | SticTest1 on the 5 guests that share the hosts' kernel (about 48 min), and the filesystem side of the other 22. One Linux guest per kernel line, plus the SELinux guests, would remain. |
| F | The fork suites as root on tmpfs (the 15 tests that skip today), run on every fork commit | a few hours | about 2 min warm | Using a guest round as the first regression gate |
| G | OS-gated unit tests run inside the build guests: `cargo test` in build-rustic-native.sh's guest, and `go test -c` binaries for freebsd, netbsd and illumos run in one boot | 1 day | minutes per OS, inside the build overlay | Replaces nothing outright; a cheap gate before the 13 non-Linux guests (about 2.0 guest-hours) |
| H | Self-tests for the judges: fixtures with one planted difference per metadata kind for fsmeta.py compare, holttest1d/judge.py and verdict.py, plus a replay of the kept r3all records | 0.5 day | seconds, on the Mac | Rerunning guests to check a judge change. Today a judge bug would make every layer pass silently. |
| I | Binary checks each round: symbol versions against the oldest supported OS release (FreeBSD 14), static linking, version strings | 1-2 hours | seconds | ABI breaks found only by a guest failing |
| J | A change-to-test map. affected.sh covers only run-all.sh's 7 platforms (affected.sh:1-35), drive.py takes explicit guest lists, and holt isn't mapped at all. Extend it to SticTest1 guests by OS and filesystem, and to holt modules (using A's plan diffs). | 1 day | seconds | Full rounds after narrow changes |

Two cheap paths already exist and could be reused:
- **ht1-debug.sh** works entirely in RAM: it backs up once, then rebuilds in 38-91 s and verifies in 38-81 s (README:244-270).
- **The RAM-first defaults** of fs_roundtrip.py, sparse-rt.sh and nonroot-rt.sh. Their writes to tmpfs and zram never reach a disk (iolog.sh:13).

## Gaps in this evidence

- **Disk writes.** The wear log covers about 5 hours of 2026-11-07, mostly HoltTest1 BSD debugging. Per-job writes of SticTest1, Linux ht1.sh, compare.py and vm-update aren't measured. Chopin's 12.8 GB is mostly unattributed. The HoltTest1 and compare.py write figures are lower bounds (kept space) or estimates (source sizes).
- **Runtimes.** The round10-era runtimes come from file times. I found nothing for holt's build time or nonroot-rt.sh's runtime.
- **Coverage.** I ran nothing, so these claims come from code, saved logs and commit messages. My mapping of restic's tests to features is by keyword search.
- **Hand-run fork tests.** Whether the OS-gated fork tests were ever run by hand, beyond what commit messages mention, is unknown.
- **Per-kernel differences.** I didn't check every Linux inner.sh for guest-only steps beyond the kernel, the guest's files and the LSM. Nor did I measure which features actually behave differently across kernel lines.
- **Swap.** tmpfs pages could reach Chopin's disk swap under memory pressure; not measured.
- **Not inventoried:** the dumps from the RAID guests' runs (raid1, raid2), r2all and r32. freebsd15-ufs and freebsd15-geli have no dumps in r3all.
- **Fork CI.** Whether GitHub Actions are enabled on the forks is unchecked; I used no GitHub access.