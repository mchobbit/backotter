# GuestTest1 and the guest tooling: evidence (researcher: guest)

**Sources:**
- Code: testvm/vm-update, vm-run, vm-lib.sh, update/in-guest.sh, update/trim.sh, sync-hosts.sh and rebuild-one.sh, on main.
- Both hosts' vms/: ls, `qemu-img info -U`, image-state, update.log.
- /bkoscratch/gt1 and /bkoscratch/build on both hosts.
- The GuestTest1 session's scratchpad, /private/tmp/claude-501/-Users-mchobbit-git-backotter/39d24434-6f21-44ae-8f2e-b828b29e8fb2/scratchpad: gt1-partB.json, gt1-st.out, gt2/REPORT.md, gt2/wf.json, upd/roll.log.
- My parsed copies are in /private/tmp/claude-501/-Users-mchobbit-git-backotter/6e7b60cc-f873-4450-892c-1b053e7a5606/scratchpad/t2/guest/ (gt1-run1-defects.txt, gt1-run2-defects.txt, qinfo-*.tsv).
- Host times are CET.

## (a) GuestTest1's checks and their cost

| Check | What it does | Measured cost | What it found |
|---|---|---|---|
| `vm-update --status` | On each host it probes for QEMU locks and overlays on every image (vm-update:164-193). It sha256s every image (:610) and compares the result with image-state and with the other host's generation (:641-648). It refuses while any QEMU has the image open, as with rocky10-lvm's "in use" in roll.log. It isn't read-only: it appends to update.log and creates .vm-update.lock (:259-263). | 1.1-5.2 min per wave of 30-44 guests (spread of start times in update.log). It hashes 213 GiB per host (161 GiB allocated on Chopin, 186 GiB on Cinqcent). | Nothing in either run: 36/36 ok (gt1-status.txt) and 40/40 ok (REPORT.md). |
| `vm-update --try` | It boots the guest with `vm-run --ram --keep --discard`, with the overlay beside the image on /bkoscratch (:372-381, :404). in-guest.sh runs the real package update. Init's uptime proves the reboot (:870-887). Then trim.sh, poweroff, and the overlay is deleted (:908-910). It checks neither host (:848-851). | 0.7-4.6 min per guest; 11-23 min per host at 2-3 jobs. The overlays are written, then deleted: 20.1 GiB in run 1 (32 guests; zorin18 alone 3771 MiB) and 7.1 GiB in run 2 (39 guests). It downloads that day's updates. DragonFly rebuilds its base whenever its branch has moved (in-guest.sh:353-420; about 25 min at 8 CPUs). | Its own pass/fail flagged only pop24 (run 1). On 11-06 it also caught ghostbsd26-zfs's trim failing with ssh 255; showing that was intermittent took 6 more runs. |
| trim inside --try | fstrim or zpool trim. bcachefs, UFS, FFS and HAMMER2 get their free space zero-filled instead (trim.sh:20-29, 58-71, 88-109). | It runs even though --try throws the overlay away. It zeroes 10,520-12,656 MiB per such guest (update.log). On the host these writes should cost metadata only, through detect-zeroes (not measured). | f2fs roots trim "/: 0 B" (mint22, aerynos). The missing zero-fill was fixed in 04ab019. |
| SticTest1 column | Run 1 ran its own drive.py round (mark gt1-rel2), plus adapted checks in /bkoscratch/gt1/checks. Run 2 copied R&R's rel4 results. | Run 1: 31 guests in 37 min on both hosts, 12,707 VM-seconds (gt1-st.out). | 31/31 and 39/39 PASS. |
| Cross-host identity | Every host has the same generation, and each host's sha256 matches its own image-state. After any merge the hashes differ between hosts (vm-update:38-40), so this is not a byte comparison. redox and sculpt2610 were hashed by hand. | Part of --status. | Nothing. |
| Agent log sweep | Agents read each --try's update.log and update-serial.log. | Run 2: the hosts' work ran 22:35-22:52, wf.json was written 23:13 and REPORT.md 00:22. That is about 1.5 h of analysis (an estimate from mtimes). | Almost every defect. |

## (b) Defects and which check found them

| Finder | Run 1 (11-05): 78 defects (3 blocking, 5 major, 20 minor, 50 cosmetic) | Run 2 (11-06/07): 64 defects (17 minor, 47 cosmetic) |
|---|---|---|
| --status and identity | 0 | 0 |
| --try failing | 1 | 0 |
| Sweep of the --try logs | 61 | 64 (30 from update-serial.log alone) |
| SticTest1 logs only | 4 | 0 |
| New-guest checks and manual probes | 10 (6 of them redox, from /bkoscratch/gt1/redox-gt1) | – |
| Reading code, gap analysis | 3, plus the missing layouts | The GELI gap; the critic found 2 guests with no SticTest1 check |

These counts are my own classification of the evidence text in gt1-partB.json and gt2/wf.json, so they are approximate.

| Defect | Found by | Outcome |
|---|---|---|
| pop24 booted Pop_OS-oldkern, whose modules the update purged (blocking) | --try failing, then update-serial.log | dd0db8e; real update u3 |
| devuan6 had no -security or -updates suites (major) | Sweep of update.log | dd0db8e |
| alpinebsd's 11.1 kernel package held the 11.0 kernel (major) | --try's uname and the serial log | Fixed upstream; u5 on 11-07 |
| ghostbsd26 is UFS, though GhostBSD installs ZFS by default (major) | Sweep plus new-guest check | ghostbsd26-zfs built |
| redox: sshd serves no logins, and both archivers crash (blocking) | Manual boot probe | Rebuilt; graded offline-only |
| freebsd14: rustic-bko needs FreeBSD 15's libc | New SticTest1 check | 22a5089 |
| omnios58's vioif NIC fails under UEFI | Manual probe (Cinqcent /bkoscratch/build/uefi-omnios58.log) | Open |
| netbsd11 re-extracted every set; openbsd8's kernel relink cut off by poweroff; tumbleweed pulled in Recommends; apt's PackageKit hook; Mint's casper | Sweep | ecd3e7f |
| ubuntu26-zfs's ESP hidden under bpool's /boot | Run 2 sweep (unmount failure), then a probe | 3e9c8ca; u2 |
| nixos2605 grew about 1 GiB per update; FFS and UFS were never trimmed | Run 2 sweep (trim lines, overlay size) | 04ab019 |
| artix-s6's unclean reboot, mx25's dirty /boot, zorin18's locales and its 1031 MiB overlay on a no-op update, pop24 78% full | Sweep | Open |
| Missing layouts (LVM; Debian 13 on UEFI with md, LUKS and LVM; FreeBSD 14; GELI; guided ZFS) | Gap analysis | 6 new guests (ecd3e7f) |

**Repeats:** by rough word overlap, 26 of run 2's 64 defects match a run-1 defect on the same guest. 13 of the other 38 are on guests built after run 1's sweep, and most of the rest are cosmetic rewordings.

## (c) What changes a guest, and what records it

| Change | What records it | Usable as a "changed" key? |
|---|---|---|
| Real vm-update | image-state on every host: the generation (a hash chain over each overlay, vm-update:635, :769), the update count and that host's sha256s (:724). A "merged on" line in the update.log of the host that ran it. The fresh-install snapshot is retaken (its date is the merge time) and the image's mtime changes. | Yes |
| No-op update | It is merged anyway. arch's u4 (11-06 10:27) printed "there is nothing to do" and still got a new generation. On 11-06 two drivers ran at once (roll.log: "manual upd-cinq-1" beside upd-chop-4). They updated 18 guests twice, 3-17 min apart, and the second updates shipped 2.2 GiB. The lock is per host (:261-265). | Gives a false "changed" |
| --try, --status | Only a start line in update.log; the result goes only to stdout. update-serial.log is rewritten on every run (:423). | No |
| Rebuild (make-*.sh, rebuild-one.sh) | build.log, build.exit, install-serial.log, rebuild-build.json and rebuild-boot.json (rebuild-one.sh:124-174). The builder's commit isn't recorded. image-state only starts at the first vm-update, which needs identical sha256s on both hosts (:626-640). | Through mtime and the missing image-state |
| Copy to the other host | Nothing | No |
| `qemu-img commit` by hand | Nothing, but --status refuses afterwards (:644-645) | Detected, not dated |
| vm-run-options, ssh-user, passphrase files, no-ssh | Nothing, and they are not part of the generation; mtime only (alpinebsd's switch to UEFI, 11-05 16:16). All 20 vm-run-options files match across the hosts. | No |
| Harness code | git only. The hosts keep no record of which commit was synced (sync-hosts.sh:47). Today their copies of the 5 tools match main (md5). | Through git log |
| Upstream repositories | Nothing. --try tests today's updates, not the image: u1, after the 11-02 rebuild, shipped 73.4 GiB; u2, about 6 h later, 5.9 GiB (Chopin ~/backotter-work/vu-logs). | No |
| Host QEMU, OVMF, kernel | Not recorded per run (Chopin was reinstalled 11-06) | No |

**There is no per-guest record of "last verified at generation X".** image-state says what an image is, not what has tested it. SticTest1's results.json holds the guest, the bin directory and the verdicts. HoltTest1's report.txt holds the phase verdicts. Neither names the image. Only timestamps compared against update.log's merges can link a result to an image.

**One affected-only retest was already done by hand:** after 04ab019 (trim.sh), the 11-07 02:1x runs re-tried 18 guests (gt2/fx*.sh).

**Changes since run 2:**
- New generations for alpinebsd, ubuntu26-zfs and nixos2605.
- Five new guests with no image-state: fedora44-raid, ubuntu26-raid, rocky10-raid5, freebsd15-mirror, omnios58-mirror.
- Commits 04ab019, 3e9c8ca and d317239 changed update or builder code.

**Dependencies a recheck must follow:**
- ht1-bsd.sh uses the live freebsd15-ufs, netbsd11 and haiku6 images as rescuers (ht1-bsd.sh:150, :470).
- Its OpenBSD, DragonFly and illumos rescuers are one-time copies (:179-264) that never pick up guest updates.
- build-holt.sh builds inside freebsd14, netbsd11, openbsd8, dragonfly6, omnios58 and haiku6 (build-holt.sh:35-41).

## (d) The guests

How to read this table:
- **Builder** is testvm/make-X.sh with its arguments.
- **GiB** is allocated on Chopin, all disks counted. Cinqcent's figures differ, for example zorin18 at 19.1 GiB.
- **2×** means the guest was updated twice on 11-06.
- **HT1** is the guest's HoltTest1 row: "All" means HoltTest1All only, and "(R)" means it is a live rescuer.
- **GT1**: r1 and r2 are the two runs' --try; f is a --try in run 1's fix phase or in the 11-06 short check.

| Guest | Builder | GiB | Last change | ST1 | HT1 | GT1 |
|---|---|---|---|---|---|---|
| aerynos | aerynos | 1.7 | u2 11-06 2× | yes | 1A | r1 r2 |
| alpine324 | bootstrap alpine | 0.3 | u3 11-06 | yes | 1A | r1 r2 |
| alpinebsd | netbsd --alpinebsd | 0.3 | u5 11-07 | yes | 1BSD | r1 r2 |
| arch | arch | 1.7 | u4 11-06 2× | yes | 1A | r1 r2 |
| artix-dinit | bootstrap | 1.2 | u4 11-06 2× | yes | 1A | r1 r2 |
| artix-s6 | bootstrap | 1.2 | u4 11-06 2× | yes | 1A | r1 r2 |
| cachyos | arch --cachyos, jfs | 1.7 | u4 11-06 2× | yes | 1C | r1 r2 |
| debian12 | debian 12.0.0 ext4 | 2.2 | u3 11-06 | yes | 1Obscure | r1 r2 |
| debian13 | debian 13.0.0 btrfs | 1.8 | u3 11-06 | yes | 1D | r1 r2 |
| debian13-stack | debian-stack | 2.6 | u2 11-06 2× | yes | 1D | f r2 |
| debian13-zfs | debian-zfs | 1.5 | u3 11-06 | yes | 1D | r1 r2 |
| devuan6 | debian --devuan | 2.4 | u5 11-06 2× | yes | All | r1 r2 |
| dragonfly6 | dragonfly | 1.4 | u4 11-06 | yes | 1BSD | r1 r2 |
| fedora44 | fedora | 4.1 | u5 11-06 2× | yes | 1F | r1 r2 |
| fedora44-raid | fedora --uefi --raid | 5.6 | built 11-07, no image-state | – | 1F (fails) | – |
| fedora44-uefi | fedora --uefi | 3.7 | u5 11-06 2× | yes | 1F | r1 r2 |
| freebsd14 | freebsd 14.5 zfs | 0.7 | u1 11-06 | yes | All | f r2 |
| freebsd15 | freebsd 15.1 zfs | 0.9 | u3 11-06 | yes | 1BSD | r1 r2 |
| freebsd15-geli | freebsd --uefi-geli | 0.8 | u1 11-06 | yes | none | f r2 |
| freebsd15-mirror | freebsd --zfs-mirror (no rebuild recipe) | 1.3 | built 11-07 | – | 1BSD | – |
| freebsd15-ufs | freebsd 15.1 ufs | 2.0 | u3 11-06 | yes | 1BSD (R) | r1 r2 |
| ghostbsd26 | ghostbsd ufs | 1.3 | u1 11-06 | yes | 1BSD | r1 r2 |
| ghostbsd26-zfs | ghostbsd zfs | 1.5 | u1 11-06 | yes | none | f r2 |
| haiku6 | haiku (no recipe) | 0.7 | u2 11-06 | yes | 1H (R) | status only |
| hardenedbsd15 | hardenedbsd | 2.1 | u1 11-06 | yes | 1BSD | r1 r2 |
| mint22 | from-live mint f2fs | 14.0 | u3 11-06 | yes | 1M | r1 r2 |
| mx25 | from-live mx ext2 | 10.7 | u4 11-06 2× | yes | 1C | r1 r2 |
| netbsd11 | netbsd 11.1 | 0.9 | u5 11-06 | yes | 1BSD (R) | r1 r2 |
| nixos2605 | bootstrap nixos | 2.4 | u5 11-07 | yes | 1Obscure | r1 r2 |
| omarchy | omarchy | 10.8 | u5 11-06 2× | yes | 1Obscure | r1 r2 |
| omnios58 | omnios | 1.9 | u3 11-06 | yes | 1I | r1 r2 |
| omnios58-mirror | omnios --rpool-mirror (no recipe) | 2.7 | built 11-07 | – | 1I | – |
| openbsd8 | openbsd | 2.7 | u3 11-06 | yes | 1BSD | r1 r2 |
| pop24 | from-live pop ext4 | 15.7 | u5 11-06 2× | yes | 1Obscure | r1 (failed) r2 |
| redox | redox (no recipe) | 0.2 | built 11-05, no image-state | – | 1Obscure (offline) | – (no SSH) |
| rocky10 | rocky | 2.4 | u4 11-06 2× | yes | 1F | r1 r2 |
| rocky10-lvm | rocky --lvm | 2.0 | u1 11-06 | yes | 1F | f r2 |
| rocky10-raid5 | rocky --raid5 | 2.7 | built 11-07 | – | 1F (fails) | – |
| sculpt2610 | sculpt | 0.05 | built 11-02, no image-state | yes (offline) | 1Obscure | – (GUI only) |
| tumbleweed | opensuse | 2.0 | u4 11-06 2× | yes | 1Obscure | r1 r2 |
| ubuntu24 | ubuntu 24.04 | 3.8 | u3 11-06 | yes | 1U | r1 r2 |
| ubuntu26-raid | ubuntu --uefi --raid | 7.2 | built 11-07 | – | 1U | – |
| ubuntu26-uefi | ubuntu --uefi | 4.6 | u4 11-06 2× | yes | 1U | r1 r2 |
| ubuntu26-zfs | ubuntu --zfs-guided | 3.5 | u2 11-07 | yes | All | f r2 |
| ubuntu26-zfsmirror | ubuntu --zfs-mirror | 12.5 | u4 11-06 2× | yes | 1U | r1 r2 |
| void-musl | bootstrap void | 2.2 | u3 11-06 | yes | 1V | r1 r2 |
| zorin18 | from-live zorin nilfs2 | 11.9 | u4 11-06 2× | yes | 1Z | r1 r2 |

The base installs of most guests come from the 11-02 rebuild through rebuild-one.sh: 31 rebuild-build.json files, 39-820 s per build.

## (e) Cheap health signals that already exist

| Signal | Cost | What it tells |
|---|---|---|
| image-state, `stat`, `qemu-img info -U` | Milliseconds | Generation, update count, last merge (the snapshot's date), allocated size |
| --status | 1-5 min; reads every image in full | Images unchanged since the last merge, and the hosts agree |
| vm-update's summary line | Free | uname after the reboot (this caught alpinebsd's kernel) and the overlay's size (this showed no-op churn: zorin18 1031 MiB, nixos2605 1030 MiB, openbsd8 466 MiB) |
| update.log | Free, but kept per host and only ever appended to (pop24's is 528 KB) | The package manager's output and trim results per mount |
| update-serial.log | Free, but holds the last run only | Boot and shutdown errors; most sweep findings came from it |
| boot-state.sh through compare.py's boot test | 12-48 s per guest, writes only to RAM (bootchk r1 on 11-03: 31 guests, all rc=0) | init, state, failed units, services, firmware, I/O stalls. vm-update never runs it, and run 2 had no failed-unit sweep. |
| rebuild-boot.json, SticTest1 results.json | Written as the build or test runs | The same boot fields, or the test verdicts and bin directory |
| /bkoscratch/wear/smart.log and jobs.log | Hourly, and per cmp-run job | SSD lifetime writes (Chopin's P3: 50.7 TB, 6% used) and what each job wrote. Started 11-07 at 04:03 UTC, after both GuestTest1 runs. |
| *.temp files beside the GuestTest1 drivers | Free | Highest Tctl (61.5 °C on Chopin in run 2) |

**Levers I noticed:**
- Skip the merge when the package manager did nothing; the generation then becomes a true "changed" signal.
- Write the generation, harness commit and bin directory into every test result.
- Run --try per guest only when its generation, its harness files or its upstream repositories have changed.
- Drop trim from --try, or set VM_UPDATE_RAM=1 so the overlays stay off the SSD.
- Add boot-state.sh to --try, and keep a per-guest baseline of known log lines so a sweep reports only new ones.
- sculpt2610 has no no-ssh marker, so vm-update would wait 900 s for SSH on it.

## Gaps in this evidence

- **Bytes written by --try and --status were never measured.** Wear logging started after both runs. Overlay apparent size is only a proxy, and zero-fill inflates it.
- **No file holds run 1's grade table.** I used gt1-partB.json and the memory note.
- **The finder counts and the repeat matching are my parse of free-text evidence**, so treat them as approximate.
- **update.log is kept per host.** Cinqcent has none for debian12 or mint22.
- **Some things I didn't check:**
  - Every make-*.sh in full (I skimmed them).
  - Whether detect-zeroes really keeps zero-fill off the disk.
  - Why the same generation is allocated differently on the two hosts.
  - redox's and sculpt2610's hashes, which I didn't re-take.
  - Whether Chopin's vms/ is bind-mounted (findmnt printed nothing).
- **haiku6, redox and sculpt2610 never had a --try** in any GuestTest1 run.