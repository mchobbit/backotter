# BackShot1All: every distro that has passed BackShot1, again, with today's tools (draft)

2026-11-13. Stored at the user's request, not yet run; the 10 open questions
still need their answers before it runs.
Revised the same day after a review against the repo. est. marks an estimate.
L is a distro's library run (its backup on mybig512), B its fresh run.

## Goal

Show that today's `bko-holt` and `rustic-bko` still do a full bare-metal
round trip on the Thinkpad for all 12 distros that passed BackShot1 there,
without reinstalling any of them. Today's `bko-holt` means main at b3af94d
or later:
- the NixOS fixes, a8d84e1 and 220c013;
- the zeroing of disk ends, 8b7e177;
- holder release with `--erase`, 4b2b4d2 and e452150;
- b3af94d, `--erase` on a disk whose table already matches (committed
  14:09, which an earlier draft listed as pending).

## Approach: no reinstalls, two halves per distro

- **A, restore the library backup with today's tools.** `to-stick` from
  whatever system is running (or straight to `phase2` if the laptop is already
  on the rescue), `phase2 L`, `reboot`, `bs1-hold on`, `verify L`. This tests:
  - load, including 8b7e177's zeroing, since bs1-restore always passes
    `--erase`;
  - rustic-bko restoring a snapshot made by an older build;
  - the boot stage;
  - all of it against L's own baseline.

  Every L has BackShot1's keys and hostname, so nothing is installed. Its
  snapshot is `latest:/mnt/bs1-src` whatever its date: before 498eaa9 the
  tree was mounted there, and since then it is backed up `--as-path` there.
- **B, a fresh round trip of the system A left,** in the same boot as A's
  verify: `preflight`, `phase1` (today's dump and backup), `to-stick`,
  `phase2 B --load-wipes` (Q3), `reboot`, `bs1-hold on`, `verify B`, then the
  distro's extra checks. This adds dump, the backup, and load's own wipe on a
  disk that already holds the same layout, which is the b3af94d case. A
  library backup of a restored system is how the 2026-11-09 five-wipe cycle
  ran (Ubuntu, Fedora), so the pattern is proven.
- **B needs A.** B backs up the system A restored, so a distro whose A can't
  pass has no B (Fix policy).
- **Runs are always named in full,** never by distro: `bs1-list --resolve`
  takes the newest backup, which isn't always the one that passed. The driver
  takes B's name from phase 1's `RUN=` line and keeps it in its state.
- **No separate `plan` step.** `phase2` reads the whole backup, makes the
  same plan and refuses before wiping. Skipping `plan` saves one full
  `check --read-data` of each backup.
- **No OS updates, and no re-adopt** unless preflight or the driver's own
  check names a missing tool. On Manjaro and EndeavourOS, bs1-adopt's
  `pacman -Sy` would be a partial upgrade, so there I ask first.
- **The holds last only for the boot.** `bs1-hold on` runs as soon as SSH
  answers after each boot of the installed system, before `verify`, and so
  also before B's phase 1, which runs in that boot. It:
  - starts `systemd-inhibit --what=sleep:idle:handle-lid-switch` as a
    transient unit;
  - masks the sleep targets with `systemctl mask --runtime`;
  - stops and runtime-masks the distro's update timers and services (table
    below).

  All of it lives in `/run`, which is in neither the tree nor a manifest, and
  a reboot undoes it. Bazzite's backup already carries its persistent hold:
  uupd.timer disabled and sleep masked since 2026-11-11 09:39 (B9's backup
  was made at 18:31 CET).
- **If an update slips in before the hold,** `verify`'s package check fails.
  The Persistent timers that matter (apt's, at least) wait a random delay
  after boot, so this is unlikely. If it happens, it counts as interference:
  A is rerun once with no fix. If it happens twice, I ask you (Fix policy).
- **One binary of each tool for every distro:** static builds of `bko-holt`
  and `rustic-bko` (Tooling 2), built once and staged once. NixOS can't run a
  dynamic glibc build. The static binary hasn't run `dump` on a glibc distro
  yet, so the `gentoo` rehearsal (Tooling 8) covers that first.

## Per-distro table

| # | Distro (stack) | L: library run for A | Held besides sleep | Console | Extra checks | Est. A+B |
|---|---|---|---|---|---|---|
| 1 | NixOS 26.05 (btrfs top level, 7 subvols, systemd-boot) | none: B only, from the running restore of -105758 (Q2) | nothing auto by default | none | in B, after verify: gen 2 by `systemctl reboot --boot-loader-entry=nixos-generation-2.conf`, `/run/booted-system` checked, then back to gen 3 | 40 min |
| 2 | Debian 12 (LUKS2+LVM, XFS) | debian-12-20261110-141705 | apt-daily{,-upgrade}.timer, unattended-upgrades | passphrase twice | both halves: LUKS UUID (`cryptsetup luksUUID`) is the baseline's | 75 min + waits |
| 3 | Bazzite 44 (ostree, composefs) | bazzite-44-20261111-173140 | uupd already held in the backup; rpm-ostreed-automatic and bootc-fetch-apply-updates (verify stops them too) | rollback once, in A (Q4) | A: rollback booted from the GRUB menu and checked by eye (its /etc has no sshd), then `check-boot L` on the default; B: `check-boot B` on the default | 150 min |
| 4 | Ubuntu 26.10 | ubuntu-26.10-20261109-203214 | apt timers, unattended-upgrades, snapd.service and snapd.socket | none | none | 70 min |
| 5 | Fedora 44 (btrfs, SELinux) | fedora-44-20261109-204845 | dnf-makecache.timer, any dnf-automatic timer, packagekit | none | SELinux labels, compared by the manifest when the baseline has xattrs | 70 min |
| 6 | Manjaro (F2FS, GRUB) | manjaro-20261109-200252 | pamac timers (pamac-mirrorlist rewrites /etc/pacman.d/mirrorlist) | none | none | 60 min |
| 7 | EndeavourOS (btrfs, systemd-boot, dracut) | endeavouros-20261110-090500 (after bs1-adopt's firewalld fix; its only restore so far was `--reformat` onto XFS, so as-is onto btrfs is new: Q5) | none | none | none | 60 min |
| 8 | Debian 13 (plain LVM) | debian-13-20261110-055903 | apt timers | none | none | 50 min |
| 9 | openSUSE Tumbleweed (btrfs+snapper, shim+sdbootutil) | opensuse-tumbleweed-20261108-20261110-165711 | snapper-timeline/cleanup, btrfs-balance/scrub/trim, packagekit | none | `snapper list` for root and home; shim fallback kept; before phase2 B, btrfs free space above twice one snapshot (5.8 GiB each, restored as full copies) | 100 min |
| 10 | Alpine 3.24 (ext4, GRUB by fallback) | alpine-3.24.4-20261110-184526 | none | none | BootCurrent is the baseline's kind (the firmware's NVMe0 fallback); before phase 1 B, `efibootmgr` is present (Chimera's `to-stick` needs it; Tooling 6) | 20 min |
| 11 | Chimera (ZFS root, dinit) | chimera-20261112-092235 | none | none | `zpool status -x`; the pool is named rpool, not rpool_bko | 45 min |
| 12 | Gentoo (OpenRC, LVM, GRUB lvmid) | gentoo-2.18-20261112-185749 | none | none | lvmid check (built into verify) | 50 min |

Each L is the newest run whose own restore passed. EndeavourOS is the
exception: it uses the one after the firewalld fix (row 7).

**Old layouts:** checked in holt/src/load/layout.rs and osrel.rs.
- `load` reads one schema, 1, and every dump ever made is schema 1.
- Every L's os id and version is in today's SUPPORTED: Ubuntu 26.10 matches
  by its major version, and Manjaro and EndeavourOS by an empty VERSION_ID.
- The old dumps' missing fields are what A exists to catch. Only ostree
  needs `backup_sources`, and Bazzite's L was made after B2.

**Old baselines:** `package_list` has changed only for Gentoo (7ee759f),
before Gentoo's L was made. verify skips lvmid where the baseline has none,
and xattrs where the manifest has none.

Total about 13 h of machine time (est.), not counting fixes.

## Order

The user block goes wherever you are at the laptop. Tooling plus NixOS take
about 5-6 h from approval (est.), so I recommend running the unattended part
overnight and the user block in the morning (Q10).

1. **First, once the tooling is staged: NixOS B, unattended.** It is already
   on the laptop, and it is the canary for the new binaries and the driver end
   to end.
2. **Unattended, about 8 h (est.), overnight:**
   - Ubuntu, Fedora and Manjaro go first. Theirs are the oldest backups,
     made by the oldest dumps and rustic-bko builds, so fixes land early and
     fewer distros have to be rerun.
   - Then EndeavourOS, Debian 13, Tumbleweed, Alpine and Chimera.
   - Consecutive A's wipe different stacks: btrfs under LUKS, LVM under ZFS,
     and so on. That is extra coverage of the script's own wipe.
3. **User block, about 2.5 h (est.):**
   - Debian 12 A and B: you type the passphrase at about +20 min and +65 min
     (est.).
   - Bazzite A, with the rollback at its end, at about +2.5 h (est.).
   - The block ends on Bazzite, which needs no passphrase, as the README
     asks. If step 2 is stopped on a fix when you're ready, I pause it at the
     next distro boundary and run the block first (`bs1-all --only`).
4. **Unattended, about 2 h (est.):** Bazzite B, then Gentoo, which is last
   and stays on the laptop (Q6).

## Tooling to build first (about 4-5 h, est.)

Chopin answers ping at 192.168.68.194, but at 14:19 it refused SSH and
chopin.local didn't resolve. So everything below runs on Cinqcent, with
Copland and Cage for parallel rehearsals. On all three the default rustc is
1.85.1, 1.94.1 is installed too, and the musl target isn't.

0. **Read-only checks first,** each from the session:
   - The laptop is on NixOS gen 3 and no other session is using it or
     mybig512 (BackShot2Alpine has used it).
   - `backshot1 list`: every L is present with `PHASE1_OK`, and the drive's
     free space is recorded.
   - The firmware's boot entries are counted (Q8).
   - Alpine's L lists `efibootmgr` in `baseline/packages.txt`.
   - `keys/disk-passphrase` is non-empty with no trailing newline.
1. **b3af94d.** It's on main, but its message records no test run and no
   review. Run the holt suite once in a warm dir on Cinqcent
   (`/bkoscratch/holt-warm/bs1all`; `main` and `review` exist there already),
   and give it a High review: it decides what gets erased. Fix what the review
   finds, as usual.
2. **The binaries,** both static the way holt/README.md documents
   (`RUSTFLAGS="-C target-feature=+crt-static"`, `--target
   x86_64-unknown-linux-gnu`), from git archives, on Cinqcent:
   - `bko-holt` from the commit after item 1, with the default 1.85.1
     (holt's rust-version is 1.85);
   - `rustic-bko` from the fork head (rustic ea44449 = 0.11.4+backotter.10,
     on core 5270ff3) with `cargo +1.94.1`. rustic's rust-version is 1.91.0,
     so the default 1.85.1 can't build it.
   - Nothing is installed in rustup. The core is a git dependency, so the
     fetch needs the network.
   - Record both sha256s.
   - The final heads' SticTest2 is still owed (chimera-support), so it runs in
     parallel (Q9).
3. **`bs1-hold on|off|status`** (on the drive): the runtime holds above. It
   does nothing on non-systemd systems, which have no automatic updates here.
   `backshot1 stage`'s file list gains it.
4. **`backshot1 find-host`** finds the installed system by MAC, as
   `find-rescue` does, for when mDNS fails (Bazzite has missed it before).
   **`backshot1 nixos-gen N`** does the generation boot and its checks.
5. **`bs1-restore --load-wipes`** (if Q3 is yes), B only:
   - The script's own `vgchange -an`, `mdadm --stop`, `zpool export`, its
     "still active" check and `wipefs` are skipped, so `bko-holt load
     --erase` releases the holders, zeroes the ends and writes the table
     itself.
   - The serial, plan, other-disks and nothing-mounted guards stay.
   - It fails unless plan.txt says the partition stage runs, and if the
     partition, format or restore stage reports "done already".
   - In every mode, bs1-restore also logs the sha256 and `--version` of both
     binaries (today it logs only `bko-holt --version`), so A's logs carry
     them too.
6. **bs1-adopt installs `efibootmgr` on Alpine.** eeab2a0 did that on
   claude/bs1-alpine but was never merged; main's adopt does it only on
   Chimera and Gentoo. It's a small change with no separate review. It is
   used only if the check in item 0 or before phase 1 finds efibootmgr
   missing, and then before phase 1, so B's backup has it and Chimera's
   `to-stick` works.
7. **`bs1-all QUEUE [--only D] [--from STEP] [--dry-run]`**, the driver on
   the Mac:
   - It runs one distro's A then B end to end, starting with `find-rescue` if
     the laptop is already on the stick.
   - It keeps per-step state in `$BS1_STATE/bs1all/`, a fixed directory
     rather than the default `$TMPDIR`, so it can resume. It writes a
     journal: `STEP OK`, `FAIL`, `WAIT-USER`, `NEED-PASSPHRASE`, `ALL-DONE`.
   - It stops at the first FAIL.
   - It runs under `caffeinate -dims`, and the session holds the Mac awake.
   - The session's Monitor watches the journal and sends the push
     notifications.
   - Before each phase 1, it checks for at least 80 GB free on the drive and
     for `efibootmgr` (`to-stick` needs it on every UEFI system).
   - Timeouts:
     - after a LUKS reboot, a push asks for the passphrase as soon as the
       reboot is sent, and again if the laptop isn't back in 10 min;
     - after any other reboot, it warns after 3 min and fails after 15;
     - for phase2, it fails after three times that distro's last restore
       time or 2 h, whichever is longer. `backshot1 phase2`'s own poll has no
       limit.
   - A WAIT-USER step (Bazzite's rollback) waits until the session writes a
     `continue` file after you answer in chat.
8. **Rehearsals,** on Cinqcent, Copland and Cage in parallel:
   - `bs1-qemu.py` gains a `--load-wipes` pass-through. It restores onto the
     guest's own disk, which is exactly B's case. It runs on:
     - `alpine324` (smoke);
     - `nixos2605` (btrfs top level);
     - `gentoo` (a glibc system running the static `dump`; the vg0 the
       rescue autoactivates, released by load; XFS home; lvmid);
     - `chimera-zfs` (the pool export).
   - The bs2 leftovers set, strict, at b3af94d (`bs2-host leftovers --run
     lo-after-b3af94d`), if bs2-host runs off Chopin. Its L3 was refused
     before, which is the held-VG case.
   - `bs1-all --dry-run`.
   - One High review covers items 3 to 7, with `--load-wipes` read first.

After that, `backshot1 stage TOOLS_DIR` runs once, from the running NixOS.

## PASS criteria

- **A distro passes when both halves pass:**
  - **A:**
    - phase2's `RESULT: PASS`: 0 differences, no stage skipped, and status
      says "every stage is done";
    - then `verify L` gives `RESULT: PASS`, including "firmware booted the
      restored ESP's entry";
    - the extra checks marked for A.
  - **B:**
    - `PREFLIGHT OK`, then `PHASE1_OK` (`check --read-data`);
    - plan.txt shows that the partition stage runs, and, on LVM and ZFS
      (Debian 13, Gentoo, Chimera), load's release step for what the rescue
      started; if the rescue started nothing, the plan says so;
    - phase2's PASS, then `verify B`'s PASS;
    - the extra checks in the table.
- **BackShot1All passes when:**
  - all 12 distros pass;
  - every restore log and every phase 1 log records the binaries' sha256:
    the staged ones, or, for a distro rerun after a fix, the final ones (Fix
    policy);
  - the laptop is left booted with `bs1-hold off` applied.

## Fix policy

The user's rule applies: "keep running, testing, and running again until you
succeed."

- **When a step fails,** the driver stops and you get a push notification. I
  diagnose from `runs/RUN/restores/STAMP/` and the bko report, then sort the
  failure:
  - **Interference** (an update, sleep, mDNS, power): rerun, no fix.
  - **An expectation in the harness** (a manifest EXPECTED path, say): a
    small fix with no separate review (fix-cycle-speedups #4).
  - **A tool bug:** fixed by a High-effort agent that has a file map. A
    change to what gets erased, mounted or restored gets its own High review.
- **Each fix:**
  - tests run once, in the warm dir;
  - commit on main, no push;
  - rebuild and restage (from the installed system, or with `scp` to the
    rescue into `/mnt/bs1-backup/backshot1/bin`);
  - then `bs1-all --from STEP` resumes that distro.
- **After a failed phase2,** the laptop sits wiped on the rescue, and the
  retry starts there.
- **Earlier distros are rerun (their B only)** when the reviewer judges that
  the fix touches a path they used.
- **I stop and ask you when:**
  - the same step fails 3 times after fixes, or interference repeats;
  - an A fails because of the old backup itself (something a fresh dump
    would have recorded): fix load, or record it as a limit and skip that
    distro (reinstalling is out of scope);
  - a fix would touch anything but the internal disk;
  - the drive falls below 80 GB free;
  - an OS update or a re-adopt on Arch would be needed.

## Your part

- **Before the start:**
  - Plug the Thinkpad's PSU in directly, not through the hub (the
    2026-11-08 low-power stall).
  - Leave the lid open and the USB Ethernet adapter in.
  - Leave the rescue stick and mybig512 plugged in, and **unplug the spare
    USB sticks** (phase2 refuses unexpected disks).
  - Keep other sessions off the Thinkpad and mybig512 until the end.
  - Keep the Mac on power. The driver keeps it awake.
  - Confirm that `keys/disk-passphrase` is still right for Debian 12.
- **During the user block:**
  - Type Debian 12's passphrase twice, when the push asks.
  - Boot Bazzite's rollback once: the second GRUB entry, ending
    `(ostree:1)` (hold Esc as GRUB starts if no menu shows). Read off
    `rpm-ostree status` (booted marker on `.0`) and `cat /proc/cmdline`,
    reboot, then tell me in chat.
- **Any time:** if a push says the laptop isn't back, tell me what's on its
  screen. If it shows no OS after a failed restore, pick the stick from the
  F12 menu.
- **At the end:** say which OS should stay (Q6).

## Risks

- **The backup drive filling.**
  - B adds one repository per distro, about the size of that distro's newest
    one. The draft's figure for all of them together was 45-60 GB (est.);
    Bazzite's repository alone may be a large part of that. Item 0 replaces
    the figure with the sum `backshot1 list` shows.
  - A rerun of phase 1 adds another.
  - The 80 GB check runs before each phase 1, and nothing on the drive is
    deleted without asking you.
- **NVMe wear.**
  - 23 restores: two of about 59 GiB for Bazzite, two of Tumbleweed with its
    snapshots as full copies, and the rest 1-10 GiB each.
  - That is about 250-350 GB (est.), a negligible fraction of a consumer
    NVMe's rated endurance.
- **Old backups.**
  - Their snapshots were made by older rustic-bko builds: A is the test of
    that, and a FAIL there is a real compatibility bug.
  - Pre-xattr baselines compare without attributes, as documented.
- **rustic-bko 0.11.4+backotter.10** changed what a restore does with
  directories on XFS: their XFS options come off and go back on. Its SticTest2
  hasn't run (Q9). Debian 12 (XFS root) and Gentoo (XFS home) are where it
  shows here.
- **Tumbleweed.**
  - The snapper snapshots come back as full copies, so its restores write
    more and take longer (known; the btrfs-shared-snapshots spec isn't
    built).
  - B's free space is checked before its phase 2 (table).
- **The rescue image.** `bs1-restore` uses the drive's `bin/`, so the stick
  supplies only:
  - the kernel and OpenZFS 2.4.1;
  - the mkfs tools;
  - the LVM, md, LUKS and ZFS tools that load's release runs.

  Keep the stick that passed NixOS today, and don't rebuild it mid-test.
- **mDNS.** Every system answers as `thinkpad` with the pinned host key, but
  avahi can lag. The driver falls back to `find-host`.
- **Firmware boot entries.**
  - load's boot stage runs `efibootmgr --create` and never removes an
    older entry of the same name. Only `to-stick` deletes its own "BKO
    rescue".
  - So each restore adds an entry: about 23 more on top of every restore
    since 2026-11-08, not "at most about 12" as the draft said.
  - A full NVRAM store can make creating an entry fail, or worse on some
    firmware (Q8).
- **LUKS.** A restored Debian 12 sitting at its prompt blocks the queue. The
  driver waits and asks; it never goes on.
- **Builds.** If Cinqcent can't fetch or build, Copland and Cage have the
  same two toolchains.

## Open questions (my recommendation first)

1. **Name:** **BackShot1All**, after HoltTest1All. Or BackShot1Lib.
2. **NixOS:** B only, from the running system: its library restores passed
   today, and B runs today's load on NixOS anyway. Or full A+B for
   uniformity (+25 min).
3. **Let `bko-holt load --erase` do the wipe in B (`--load-wipes`):** yes, B
   only. b3af94d is in, and this is the only way this test exercises holder
   release and the matching-table case on real hardware. A keeps the
   script's own wipe.
4. **Bazzite's rollback:** in A, right after `verify L`. It is B9's own
   check against B9's baseline, now with today's load, and it brings the user
   block down from about 4 h to about 2.5 h (est.). Or in B, which also
   covers today's ostree dump but keeps you about 1.2 h longer (est.). Or in
   both, a second console visit. NixOS's generation check stays in B: it needs
   no console.
5. **EndeavourOS's XFS `--reformat` variant:** skip it. `--reformat` is on
   claude/holt-reformat, not main. Or merge it first and add one more EOS
   restore.
6. **What stays on the laptop:** Gentoo, the last in the order. Or name
   another, for one more A restore (15-75 min). If it's Bazzite, its
   backup's hold stays on until you undo it (bazzite-support).
7. **Builds:** Cinqcent, static through `+crt-static` on the gnu target, as
   holt/README.md documents, with the 1.94.1 toolchain it already has for
   rustic. Nothing changes in rustup. Or add `x86_64-unknown-linux-musl` to m's
   rustup on Cinqcent, for both toolchains, which is what NixOS's static
   build used on Chopin.
8. **Firmware entries:** count them in item 0. Below 30, leave them. At 30 or
   more, prune once before the start, with your OK: only entries whose
   partition GUID is on no present disk, never "BKO rescue" or the
   firmware's own. Or never prune.
9. **rustic-bko:** the fork head (backotter.10), with the owed SticTest2 of
   the final heads run during the tooling. The XFS restores (Debian 12,
   Gentoo) wait for its result. Or keep the build that passed NixOS today,
   whose version item 0 records.
10. **When the user block runs:** in the morning, after the overnight part
    (Order). Or tonight, right after NixOS B, if you'd rather be at the
    laptop late in the evening (about 20:00-22:30, est.).
