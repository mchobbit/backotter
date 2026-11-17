# BackShot1All: every distro with a passing backup on mybig512, again, with today's tools (draft)

2026-11-17: cut to the seven distros with backups on the rebuilt mybig512.
The drive was wiped and rebuilt on 2026-11-16 (XFS, label mybig512), and
every library run of the old table went with it. Its seven passing runs of
2026-11-16 are the new table; the other distros wait for a reinstall
(Dropped rows). The attended block, the Bazzite rollback and the builds are
gone, and A and B reuse `backshot1 cycle` (Q3). The open questions are now 5,
and still need their answers before it runs.

2026-11-13: first drafted and stored at the user's request; revised the same
day after a review against the repo. Not yet run.

est. marks an estimate. L is a distro's library run (its backup on mybig512),
B its fresh run.

## Goal

Show that today's `bko-holt` and `rustic-bko` do a full bare-metal round
trip on the Thinkpad for all seven distros with a passing backup on
mybig512, from that backup and from a fresh one, without reinstalling any of
them. Today's tools are the ones staged under `backshot1/bin` on the drive:
- `bko-holt` 0.2.6, static-pie, from `~/backotter-work/holt-bin/linux/` on
  Chopin;
- `rustic-bko` 0.11.4+backotter.16, static.

Main's scripts include 043ea1a (LUKS family N, NixOS) and fe9ef31 (adopt
installs efibootmgr and loads xfs on Alpine; DRIVE loads xfs before
mounting). 60ce2b8, docs only, records family N's Thinkpad proof and is the
one unpushed commit on 2026-11-17.

## Approach: no reinstalls, two halves per distro

- **A, restore the library backup with today's tools:** `backshot1 cycle L
  1 --erase-serial S7XMNF0Y787118 --hold` from the installed system that is
  running (Q3). That is `to-stick`, `plan`, `phase2 L`, `reboot`,
  `wait-host`, `bs1-hold on` (Tooling 3, 6), `verify L`. This tests:
  - load, including the zeroing of disk ends, since bs1-restore always
    passes `--erase`;
  - rustic-bko restoring the snapshot;
  - the boot stage;
  - all of it against L's own baseline.

  Every L has BackShot1's keys and hostname, so nothing is installed. Its
  snapshot is `latest:/mnt/bs1-src`.
- **The library is a day old.** Every L was made and restored on
  2026-11-16, most likely by the binaries staged now: item 0 compares each
  L's `phase1.log` sha256 lines (bs1-backup logs both binaries' sha256) with
  the staged ones. So A is chiefly how each distro gets back onto the laptop
  for B, and the test's weight is in B. If a sha256 differs, A is again a
  test of an older build's backup.
- **B, a fresh round trip of the system A left,** in the same boot as A's
  verify: `preflight`, `phase1` (today's dump and backup), then `backshot1
  cycle B 1 --erase-serial S7XMNF0Y787118 --hold --load-wipes` (Q2, Q3), then
  the distro's extra checks. This adds dump, the backup, and load's own wipe
  on a disk that already holds the same layout, which is the b3af94d case:
  load zeroing the ends and writing a table that matches the one there. It
  doesn't test holder release: the rescue opens no LUKS volume (bs1-restore
  gives load the key only through `--key-file`) and imports no pool (the
  stick masks zfs-import-cache, zfs-import-scan and zfs-mount), and none of
  the seven has LVM or md on a plain partition, so on all seven there is
  nothing to release. Holder release stays with the bs2 leftovers set, which
  seeds its own holders. A
  library backup of a restored system is how the 2026-11-09 five-wipe cycle
  ran (Ubuntu, Fedora), so the pattern is proven.
- **B needs A.** B backs up the system A restored, so a distro whose A can't
  pass has no B (Fix policy). NixOS is the exception if Q1 is yes: the
  laptop runs its restore of L now.
- **Runs are always named in full,** never by distro: `bs1-list --resolve`
  takes the newest backup, which isn't always the one that passed: each B
  adds a newer run of its distro, and the drive also holds excluded runs
  (below). The driver takes B's name
  from phase 1's `RUN=` line and keeps it in its state.
- **No OS updates, and no re-adopt** unless preflight or the driver's own
  check names a missing tool. On CachyOS, bs1-adopt's `pacman -Sy` would be
  a partial upgrade, so there I ask first.
- **The holds last only for the boot.** `bs1-hold on` runs as soon as SSH
  answers after each boot of the installed system, before `verify`, and so
  also before B's phase 1, which runs in that boot. It:
  - starts `systemd-inhibit --what=sleep:idle:handle-lid-switch` as a
    transient unit;
  - masks the sleep targets with `systemctl mask --runtime`;
  - stops and runtime-masks the distro's update timers and services (table
    below).

  All of it lives in `/run`, which is in neither the tree nor a manifest, and
  a reboot undoes it. bs1-backup and bs1-verify already stop the snapshot
  timers (bs1-common `SNAPSHOTTERS`) and the ostree and ABRoot updaters
  (`UPDATERS`), but none of the seven's own updaters.
- **If an update slips in before the hold,** `verify`'s package check fails.
  The Persistent timers that matter (apt's, at least) wait a random delay
  after boot, so this is unlikely. If it happens, it counts as interference:
  that half is rerun once with no fix. If it happens twice, I ask you (Fix
  policy).
- **One binary of each tool for every distro:** the static builds already on
  the drive. They most likely ran every phase 1 and restore of 2026-11-16,
  NixOS's and Alpine's (musl) included (item 0), so no rehearsal of the
  binaries themselves is owed.
- **Nobody types anything.** Debian 13 and NixOS unlock from the drive
  (`UNLOCK=drive`, families D and N), and the other five have no LUKS. The
  old attended block and its console column are gone. The one console visit
  left is NixOS's older generation, and only if Q4 says to boot it.

## Per-distro table

| # | Distro (stack) | L: library run for A | Repo | Held besides sleep | Extra checks | Est. A+B |
|---|---|---|---|---|---|---|
| 1 | NixOS 26.05 (Calamares: LUKS2 xfs root, LUKS2 swap, systemd-boot, systemd stage 1; family N) | nixos-26.05-20261116-210139; the laptop runs its restore now, so B only (Q1) | not recorded here; item 0 records it | nothing auto by default | before B, on the running restore (Q4): the generations listed by reading (`/nix/var/nix/profiles/system-*-link`, `bootctl list`), each one's closure whole in the store, and an older one booted by `systemctl reboot --boot-loader-entry=...`, `/run/booted-system` checked, then back to the default; B, in a boot the drive's key opened (Order 1): verify's unlock check (`UNLOCK=drive`), LUKS UUIDs the baseline's | 36 min (A 11, B about 23-25) |
| 2 | Debian 13 (guided LVM-on-LUKS2: VG thinkpad-vg with xfs root, ext4 var and srv, swap; family D keyscript) | debian-13-20261116-151543 | 3.5G | apt-daily{,-upgrade}.timer, unattended-upgrades if installed | both halves: LUKS UUID (`cryptsetup luksUUID`) the baseline's, verify's unlock check (`UNLOCK=drive`); B: plan.txt shows the partition stage runs and nothing to release (the rescue opens no LUKS, so thinkpad-vg is never active) | 30 min |
| 3 | CachyOS (ZFS root, Limine, no LUKS) | cachyos-20261116-163026 | 6.6G | none known (item 0 lists the active timers) | `zpool status -x`; the pool keeps the source's name (the baseline's), not rpool_bko; B: plan.txt shows no pool to export (the stick never imports one) | 30 min |
| 4 | Fedora 44 Xfce (btrfs / and /home subvols, ext4 /boot, vfat ESP, no LUKS) | fedora-44-20261116-141133 | 3.1G | dnf-makecache.timer, any dnf-automatic timer, packagekit | SELinux labels, compared by the manifest when the baseline has xattrs | 25 min |
| 5 | Ubuntu 24.04.5 (ext4 /, vfat ESP, shim+GRUB, no LUKS) | ubuntu-24.04-20261116-133442 | 1.4G | apt timers, unattended-upgrades, snapd.service and snapd.socket | none | 20 min |
| 6 | Zorin OS 18 (UEFI, plain ext4 /, vfat ESP) | zorin-18-20261116-181641 | 4.3G | apt timers, unattended-upgrades, packagekit, snapd if present | none beyond verify: its first verify on 2026-11-16 failed only on `var/lock/{asound.state,card0}.lock`, expected since f49da8b; the re-verify passed, so A is the first whole run with that rule | 30 min |
| 7 | Alpine 3.24 (ext4, OpenRC, UEFI) | alpine-3.24.4-20261116-192831 | 362M | none | before B's phase 1, `efibootmgr` is present and xfs loads at boot: L was taken after fe9ef31's adopt, so the restored system mounts mybig512 with no `modprobe` by hand | 15 min |

Each L is the newest run whose own restore passed, by the 2026-11-16 list:

| Run | Last restore (2026-11-16) |
|---|---|
| ubuntu-24.04-20261116-133442 | PASS, 5 min round |
| fedora-44-20261116-141133 | PASS, 7 min |
| debian-13-20261116-151543 | PASS, 8 min, unattended |
| cachyos-20261116-163026 | PASS, 9 min |
| zorin-18-20261116-181641 | FAIL on var/lock only, re-verify PASS after f49da8b |
| alpine-3.24.4-20261116-192831 | PASS, 4 min, after fe9ef31 |
| nixos-26.05-20261116-210139 | PASS, 10 min, hands-free cycle |

**Excluded runs on the drive.** They stay where they are (nothing on the
drive is deleted without asking you), but no step names them:
- alpine-3.24.4-20261116-192621: its baseline was taken before adopt
  installed efibootmgr; never restored.
- nixos-26.05-20261116-200906: attended LUKS, before family N.
- nixos-26.05-20261116-205909: an accidental duplicate; never restored.

**Timings.** A is one cycle round, as timed on 2026-11-16 (Zorin's round
wasn't given: 8 min, est., from its repo size). B is the preflight, phase 1
(est. about one round) and one round, so about twice A, plus 2 min. Each
round also runs `plan`, one more `check --read-data` of the backup, about
1 min at these sizes (est.; Q3). So a round of R min gives A about R+1 and
B about 2R+3; the table rounds A+B up to 5 min.

Total about 3 h 5 min of machine time (est.), not counting fixes.

**Dropped rows: to return when reinstalled and BackShot1 passes again.**
They have no backup on mybig512 since the 2026-11-16 wipe. Each joins the
table, with its stack, holds and extra checks from the 2026-11-13 draft (git
history of this file, 08f427f and earlier: the committed version before
2026-11-17), once a fresh install of it
passes BackShot1 on the Thinkpad:
- from the old table: Void (musl, btrfs, GRUB), Debian 12 (LUKS2+LVM, XFS),
  Bazzite 44 (ostree; its rollback needs the console), Ubuntu 26.10,
  Manjaro, EndeavourOS, openSUSE Tumbleweed (snapper), Chimera (ZFS, dinit),
  Gentoo (OpenRC, LVM), PikaOS 4 (rEFInd, booster), KDE neon 24.04; the old
  rows for Fedora 44, Debian 13 (plain LVM), Alpine and NixOS (btrfs, 7
  subvols) are replaced by the new installs above;
- passed BackShot1 on the Thinkpad after the draft but never in the table:
  AnduinOS 2.0.5 (ad843f2), Guix (c173020), and CachyOS's earlier LUKS
  btrfs install (now ZFS, row 3);
- pending as before: Vanilla OS 3 (ABRoot's two roots; no Thinkpad pass
  yet, and its other-root boot needs the console).

## Order

All seven are unattended, so the old user block collapses. The front-loading
rule (2026-11-13: what needs a typed passphrase or a console visit goes
first) now touches only NixOS's older generation, if Q4 boots it.

1. **NixOS first, about 30 min (est.).** The laptop runs it now (the restore
   of 210139). The generation check (Q4), then B. B's phase 1 runs in a boot
   the drive's key opened, after Q4's older-generation visit: if `backshot1
   unlock` says `UNLOCK=attended` (the drive-unplugged boot recorded in
   60ce2b8 may still be the one running), reboot once with the drive in and
   check for `UNLOCK=drive` before phase 1, or `cycle B` refuses the run. It
   is also the canary for the new tooling and the driver end to end.
2. **Then the biggest layouts, while the fixes are cheap:** Debian 13 (LVM
   on LUKS), then CachyOS (ZFS root). They have the most for load to
   recreate over its own layout, and the most for verify to compare.
3. **Then the rest:** Fedora, Ubuntu, Zorin, Alpine. Alpine is last and stays
   on the laptop (Q5).
4. Consecutive A's wipe different stacks: LVM on LUKS under ZFS, ZFS under
   btrfs, and so on. That is extra coverage of the script's own wipe.

## Tooling to build first (about 3-4 h, est.)

Builds and rehearsals go to Chopin, Cinqcent, Cage or Copland through
`testvm/pick-host`. No binary is built: today's are on the drive.

0. **Read-only checks first,** each from the session:
   - The laptop runs NixOS (the restore of 210139: `/run/booted-system`),
     and no other session is using it or mybig512. `backshot1 unlock`'s
     answer is recorded: if it says `UNLOCK=attended` (the drive-unplugged
     boot of 2026-11-17), Order 1's reboot with the drive in comes before B's
     phase 1.
   - `backshot1 list`: the seven L's are present with `PHASE1_OK`, NixOS's
     repo size, and the drive's free space, recorded.
   - Each L's `baseline/unlock.txt` (`drive` for Debian 13 and NixOS, `none`
     for the rest) and its layout's firmware mode, `uefi`: what `cycle`
     refuses otherwise.
   - The staged binaries: `--version` (0.2.6, 0.11.4+backotter.16) and
     sha256, against each L's `phase1.log` and Chopin's holt-bin.
   - The firmware's boot entries, counted (Risks).
   - The hands-free set-up (README, "The hands-free cycle"): the stick in a
     laptop port, `BootMode` `Diagnostics`.
   - The stick's `/cdrom/.disk/backotter.json`, recorded.
   - `keys/disk-passphrase` is non-empty with no trailing newline (Debian
     13's and NixOS's fallback).
   - On each distro, at its first boot, the active timers (`systemctl
     list-timers`), for the holds.
1. **The `--erase` path.** b3af94d (`--erase` on a disk whose table already
   matches) and d965dc5 (table-less disks) record no review in git. If Q2 is
   yes, B relies on them alone, so they get one High review at the staged
   version, and the holt suite runs once in a warm dir. Fix what the review
   finds, as usual.
2. **The binaries:** none to build. A fix to either tool is rebuilt the way
   the staged one was (bko-holt static-pie into Chopin's holt-bin) and
   restaged (Fix policy).
3. **`bs1-hold on|off|status`** (on the drive): the runtime holds above. It
   does nothing on non-systemd systems (Alpine), which have no automatic
   updates here. `backshot1 stage`'s file list gains it.
4. **`backshot1 find-host`** finds the installed system by MAC, as
   `find-rescue` does, for when mDNS fails. **`backshot1 nixos-gen N`** does
   the generation listing, the closure check and, if Q4 says so, the
   generation boot.
5. **`bs1-restore --load-wipes`** (if Q2 is yes), B only:
   - The script's own `vgchange -an`, `mdadm --stop`, `zpool export`, its
     "still active" check and `wipefs` are skipped, so `bko-holt load
     --erase` releases any holders (none on these seven), zeroes the ends
     and writes the table itself.
   - The serial, plan, other-disks and nothing-mounted guards stay.
   - It fails unless plan.txt says the partition stage runs, and if the
     partition, format or restore stage reports "done already".
   - In every mode, bs1-restore also logs `rustic-bko --version` and both
     binaries' sha256, as bs1-backup does (today it logs only `bko-holt
     --version`), so A's logs carry them too.
6. **`backshot1 cycle` gains `--hold` and `--load-wipes`** (if Q3 is yes):
   `--hold` runs `bs1-hold on` between `wait-host` and `verify`;
   `--load-wipes` passes through to `plan` and `phase2`.
7. **`bs1-all QUEUE [--only D] [--from STEP] [--dry-run]`**, the driver on
   the Mac:
   - Per distro: A's cycle, the extra checks for A, preflight, phase 1, B's
     cycle, the extra checks for B. NixOS (Q1) starts at the generation
     check.
   - It keeps per-step state in `$BS1_STATE/bs1all/`, a fixed directory
     rather than the default `$TMPDIR`, so it can resume. It writes a
     journal: `STEP OK`, `FAIL`, `WAIT-USER`, `NEED-PASSPHRASE`, `ALL-DONE`.
   - It stops at the first FAIL.
   - It runs under `caffeinate -dims`, and the session holds the Mac awake.
   - The session's Monitor watches the journal and sends the push
     notifications.
   - Before each phase 1, it checks for at least 80 GB free on the drive and
     for `efibootmgr` (`to-stick` needs it on every UEFI system).
   - Timeouts: `cycle`'s own (`to-stick` 7 min, `wait-host` 10 min, the
     stick silent 10 min), with `BS1_RESTORE_LIMIT=60` (the longest round on
     2026-11-16 was 10 min). A system that isn't back fails `wait-host` at
     10 min, which fails the cycle: a FAIL and a push. For Debian 13 and
     NixOS, `drive` systems, that most likely means the prompt (the drive's
     key failed).
   - The firmware's boot entries: `cycle` compares each round only with its
     own round 1, so with one round per cycle its safety net never fires. The
     driver counts them after every cycle and stops once there are more than
     8 above item 0's count.
   - A WAIT-USER step (NixOS's older generation, if Q4 boots it) waits until
     the session writes a `continue` file after you answer in chat.
8. **Rehearsals,** in parallel:
   - `bs1-qemu.py` gains a `--load-wipes` pass-through. It restores onto the
     guest's own disk, which is exactly B's case. It runs on:
     - `alpine324` (smoke);
     - `nixos2605`;
     - `ubuntu26-luks` (LVM on LUKS recreated over itself, with nothing to
       release, as on Debian 13; there is still no Debian initramfs-tools
       LUKS guest, README "Rehearsal");
     - `cachyos-zfs` (a ZFS root recreated over its own pool, none
       imported).
   - The bs2 leftovers set, strict (`bs2-host leftovers --run
     lo-load-wipes`). Its L3 was refused before, which is the held-VG case.
   - `bs1-all --dry-run`.
   - One High review covers items 3 to 7, with `--load-wipes` read first.

After that, `backshot1 stage TOOLS_DIR` runs once, from the running NixOS,
so the drive's scripts are main's. TOOLS_DIR is a directory on the Mac
holding copies of the drive's own `backshot1/bin/bko-holt` and `rustic-bko`,
fetched first with `scp`, so the staged sha256s don't change.

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
    - plan.txt shows that the partition stage runs and, on all seven,
      nothing to release (the rescue opens no LUKS and imports no pool);
    - phase2's PASS, then `verify B`'s PASS;
    - the extra checks in the table.
  - NixOS, if Q1 is yes: B and the generation check only.
- **BackShot1All passes when:**
  - all seven distros pass;
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
  - **An expectation in the harness** (a manifest EXPECTED path, like
    f49da8b's var/lock): a small fix with no separate review
    (fix-cycle-speedups #4).
  - **A tool bug:** fixed by a High-effort agent that has a file map. A
    change to what gets erased, mounted or restored gets its own High review.
- **Each fix:**
  - tests run once, in the warm dir;
  - commit on main, no push;
  - rebuild and restage (from the installed system, or with `scp` to the
    rescue into `/mnt/bs1-backup/backshot1/bin`);
  - then `bs1-all --from STEP` resumes that distro.
- **After a failed phase2,** the laptop sits wiped on the rescue, and the
  retry starts there (`find-rescue`, then that half's steps by hand: `cycle`
  starts from the installed system).
- **Earlier distros are rerun (their B only)** when the reviewer judges that
  the fix touches a path they used.
- **I stop and ask you when:**
  - the same step fails 3 times after fixes, or interference repeats;
  - an A fails because of the backup itself (something a fresh dump would
    have recorded): fix load, or record it as a limit and skip that distro
    (reinstalling is out of scope);
  - a fix would touch anything but the internal disk;
  - the drive falls below 80 GB free;
  - item 0 counts 30 or more firmware boot entries;
  - an OS update, or a re-adopt on CachyOS, would be needed.

## Your part

- **Before the start:**
  - Plug the Thinkpad's PSU in directly, not through the hub (the
    2026-11-08 low-power stall).
  - Leave the lid open and the USB Ethernet adapter in.
  - Leave the rescue stick in one of the laptop's own ports (README, "The
    hands-free cycle") and mybig512 plugged in, and **unplug the spare USB
    sticks** (phase2 refuses unexpected disks).
  - Keep other sessions off the Thinkpad and mybig512 until the end.
  - Keep the Mac on power. The driver keeps it awake.
- **At the start, only if Q4 boots NixOS's older generation:** type the
  passphrase once, when the push asks (that generation's initrd predates
  family N).
- **Any time:** if a push says the laptop isn't back, tell me what's on its
  screen. If it shows no OS after a failed restore, pick the stick from the
  F12 menu.
- **At the end:** say which OS should stay (Q5).

## Risks

- **The backup drive filling.**
  - B adds one repository per distro, about the size of L's: 19.3G for the
    six with sizes listed, plus NixOS's (est. 20-25 GB in all). Item 0 has
    the free space.
  - A rerun of phase 1 adds another.
  - The 80 GB check runs before each phase 1, and nothing on the drive is
    deleted without asking you.
- **NVMe wear.** 13 restores (six A's, seven B's) of a few GiB to about
  15 GiB each (CachyOS): under 150 GB (est.), a negligible fraction of a
  consumer NVMe's rated endurance.
- **The rescue image.** `bs1-restore` uses the drive's `bin/`, so the stick
  supplies only:
  - the kernel and OpenZFS;
  - the mkfs tools;
  - the LVM, md, LUKS and ZFS tools load runs to recreate the layout.

  Keep the stick that passed the seven on 2026-11-16 (item 0 records its
  `backotter.json`), and don't rebuild it mid-test.
- **mDNS.** Every system answers as `thinkpad` with the pinned host key, but
  avahi can lag. The driver falls back to `find-host`.
- **Firmware boot entries.** The restore's boot stage now deletes any entry
  for the same partition and loader before making the source's again, and
  `to-stick` deletes its own "BKO rescue", so the count should hold steady.
  The driver's count across cycles (Tooling 7) is the safety net; a full
  NVRAM store can make creating an entry fail, or worse on some firmware.
- **LUKS.** Debian 13 and NixOS unlock from mybig512, so one sitting at its
  prompt means the drive's key failed: a FAIL and a push, not a wait. The
  queue can't go on past it, since the next step starts from that system.
- **Alpine and xfs.** A fresh Alpine boot needs xfs loaded by hand before
  its first mount of mybig512; L was taken after adopt fixed that for later
  boots, and DRIVE now loads xfs itself, so the restore of L shouldn't need
  it. If it does, that is a FAIL of the extra check, not a hand fix.

## Decisions (2026-11-17)

The user answered Q1-Q5 on 2026-11-17; nothing is open. Where the text above says "if Q1 is
yes" and the like, these govern.

1. **NixOS: full A+B** (the user's choice). A restores 210139 with today's
   tools like every other row; NixOS's row is about 36 min (est.), the total
   about 3 h 5 min (est.).
2. **`--load-wipes` in B:** yes, B only, after item 1's review (my
   judgment, as recommended).
3. **Reuse `backshot1 cycle` for A and B:** yes, with `--hold` and
   `--load-wipes` (my judgment, as recommended).
4. **NixOS's older generations: no boot** (the user, 2026-11-17: BackShot1
   is a bare-metal restore; booting a previous generation tests NixOS's boot
   loader, not bko). Kept, hands-free and seconds long, in verify of both A
   and B: every `nixos-generation-N.conf` entry on the ESP finds its kernel
   and initrd there (bs1-manifest excuses `boot/EFI/*`, so nothing else
   checks them). Options (a) and (c) of the earlier draft are dropped.
5. **What stays on the laptop:** doesn't matter (the user); Alpine, the
   last in the order.

Settled or dropped from the 2026-11-13 draft:
- the name (old 1): BackShot1All, as you call it;
- Bazzite's rollback (old 4), EndeavourOS's XFS `--reformat` variant (old
  5) and when the user block runs (old 10): their distros or block are gone;
- the builds (old 7) and the rustic-bko build (old 9): the staged binaries
  are today's tools, and nothing is built;
- pruning firmware entries (old 8): the boot stage no longer piles them up;
  the 30-entry line is now a stop-and-ask in the Fix policy.
