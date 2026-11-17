# SticTest1: targeted per-guest checks of restic-bko and rustic-bko

SticTest1 is a guest-by-guest test-and-fix plan for BackOtter's two archivers.
It checks that restic-bko and rustic-bko:

- keep every piece of file metadata BackOtter needs on each guest OS;
- write the same repository format (their snapshots are node-identical);
- restore each other's snapshots.

Each guest gets a targeted fixture test, never a whole-disk backup and restore.
The fixture is built on a scratch file system of the guest's own type. Where the
OS and file system support them, it covers:

- xattrs, POSIX and NFSv4 ACLs, SELinux labels and capabilities;
- Linux inode flags, BSD file flags, and ZFS/btrfs/bcachefs/XFS/FAT options;
- project IDs, birth times, and owners with no name;
- set-id and sticky modes, hard links, sparse files, devices (incl. rdev 0),
  FIFOs and symlinks;
- non-UTF-8 names, deep and empty directories, and the directories above the
  backed-up path.

Real examples are copied from the guest's own files. Each check then:

1. runs restic->restic, rustic->rustic, restic->rustic and rustic->restic into
   fresh file systems;
2. restores once more over existing immutable/append-only files;
3. runs one non-root backup and restore;
4. compares the results with fsmeta.py, or the OS's own tools where it doesn't run.

The user's decisions:

- Every backup and restore passes BackOtter's flags, as BackOtter does (the
  forks keep their upstream's defaults): `restic-bko backup --with-atime`,
  `rustic-bko backup --set-atime yes`, `rustic-bko restore --numeric-id`.
- The tools behave identically. Accepted differences are listed in each fork's
  BACKOTTER.md.
- Both tools skip sockets.
- On JFS and nilfs2, both mark files with holes as sparse by block count.
- Haiku is checked with rustic-bko only, because restic isn't ported there.
- Sculpt OS (Genode) can run neither tool. Its check tests the offline path from
  Linux on the guest's own ext2 partition.

## The scripts

Each guest has a directory here, `GUEST/`, with its check script `check.sh`
and the helpers it needs beside it: inner.sh, judge.py, fixture.sh, real.sh,
extra(s).sh, guest.sh, nracl.py and others. Shared helpers come from
testvm/compare (fsmeta.py, rt-common.sh, guest-slot.sh, cmp-run.sh). arch keeps
its own older fsmeta.py, so its verdicts stay as they were.

`testvm/sync-hosts.sh` copies testvm/ to the hosts, so the scripts run there
from `~/backotter-work/testvm/stictest1/GUEST/check.sh`. They replace the old
copies in `~/backotter-work/stictest1/GUEST/`, which hold the earlier rounds'
logs, results and drivers. A run reads its inner phase and helpers from
`~/backotter-work/testvm/stictest1/GUEST/` after it waits for a slot, so a
sync during a run can hand it newer ones: don't sync while SticTest1 runs.
A copy run from a worktree takes its own GUEST/ helpers, but testvm/compare,
vm-run and cmp-run.sh still come from main's synced `~/backotter-work`,
unless `BKO_TESTVM` names another testvm/ (below).

SticTest1's split of the guests between the two big hosts, from before drive.py's
queue, was this (drive.py places from one queue over the round's hosts, below;
testvm/HOSTS.md has every host and what stays on the big ones):

| Host | Guests |
|---|---|
| Chopin | aeon, anduinos2, nixos2605, chimera, garuda, biglinux, gentoo, guix15, kali, microos, pikaos4, proxmox9, truenas25, dragonfly6, ghostbsd26, ghostbsd26-zfs, hardenedbsd15, hardenedbsd15-zfs, kinoite44, ubuntu26-zfs, ubuntu26-uefi, ubuntu26-zfsmirror, ubuntu24, ubuntu24-uefi, debian12, devuan6, elementary8, zorin18, mint22, debian13, pop24, mx25, sculpt2610, slackware15, qubes43 |
| Cinqcent | aerynos, alpine324, alpinebsd, freebsd14, freebsd15-geli, arch, artix-dinit, artix-s6, cachyos, chimera-uefi, chimera-zfs, debian13-stack, debian13-zfs, elementary8-luks, fedora44, fedora44-uefi, freebsd15, freebsd15-ufs, garuda, gentoo-systemd, haiku6, netbsd11, omarchy, omnios58, openbsd8, openbsd8-crypt, guix15-bios, leap16, pikaos4-luks, proxmox9-mirror, rocky10, rocky10-lvm, slackware15-uefi, solus49, tumbleweed, uc24, vanilla3, void-musl, void-zbm |

## Running one

    ~/backotter-work/testvm/stictest1/GUEST/check.sh BIN_DIR PORT PREFIX [NEED_FILTER]

- `BIN_DIR` holds restic-bko and rustic-bko for the guest's OS, for example
  `~/backotter-work/round22/bin/linux` (or freebsd, netbsd, openbsd, omnios,
  dragonfly, haiku).
- `PORT` is a free SSH port in your own range: Chopin 2700-2799, Copland
  2800-2899, Cinqcent 2900-2999, Cage 3000-3099, and Czerny 3100-3199 once enabled (the
  host's `slot_range` in testvm/hosts.json, guest-slot.sh's `slot_range_here`).
- `PREFIX` names your scratch: `/mnt/bko-ram/PREFIX`. Make it a plain name:
  letters, digits, `.`, `_` and `-`, starting with a letter or digit.

What the script does:

- It takes a guest slot (guest-slot.sh) and room (cmp-run.sh --reserve) by itself.
- It boots a throwaway `vm-run --snapshot` overlay, so it never writes the
  guest's own disk image.
- It prints one line per checklist item: `PASS need: detail`, `FAIL need: detail`,
  or `LIMIT need: reason` (an OS or file system limit documented in BACKOTTER.md).
- It writes results.json and logs to `~/backotter-work/stictest1/GUEST/logs/`,
  outside the synced tree, or to `$STC_LOGS` if it is set. `$STC_LOGS`
  replaces that one guest's logs directory, so set it per guest. Most also
  copy the latest results.json to the top of that directory.
- It exits 0 if nothing FAILs, and cleans up after itself.
- ubuntu26-zfsmirror and aerynos keep their scratch (disks, QEMU's
  overlays) in `/mnt/bko-ram/PREFIX/GUEST`, under their `--reserve` (23
  and 2 GiB, estimates), and log the run's peak use of it as
  `ramdisk-peak-kib` in the log dir. If the check's shell is killed
  outright, their sampler still stops the VM and removes that directory.
- Most guests finish in 2 to 15 minutes.

A recheck or a regression run is the same command with a newer round's
BIN_DIR. The round 22 driver used for the final regression is
`~/backotter-work/stictest1/final-r22/drive.sh` on each host; it runs the old
copies.

## Running a round

`drive.py` (here, run on the Mac) runs every guest's check.sh as one round,
from one queue for the round's hosts (`--hosts`, of testvm/hosts.json's enabled
hosts; default its default hosts, every amd64 one: Copland and Cage joined
2026-11-14, Choubox 2026-11-17): longest guest first, each
started the moment a host has a place, a guest slot, the guest's --reserve room after
cmp-run.sh's waiting line, our free ports (each host's `stic_ports`: Chopin 2780-2799,
Cinqcent 2980-2999, Copland 2880-2899, Cage 3080-3099, Choubox 3380-3399) and Tctl at
most 90 C. A guest whose --reserve is over a host's ceiling (cmp-run.sh's
`ceiling()`: 9.0 10.1 on Copland and Cage, 3.0 6.0 on Choubox) never starts
there; one over every host's ceiling is named TOO BIG and never starts, and
the round ends without it.  A guest never starts on a host without its image
(`vms/GUEST/*.qcow2`, a cache host's link included: the small hosts' caches hold
the published guests only); one that no host with its binaries has is named NOVM
(log, summary, state.json's `novm`) and the round ends without it too.  TOO BIG
counts only the hosts with both the guest's binaries and its image. Starts are
detached on the host and
watched through their exit files every 15 s; the results land in
`~/backotter-work/stictest1/MARK/results.tsv` on the Mac. See `drive.py
--help`.

    stick/build-round.sh ROUND                          # roundROUND/bin on Chopin and Cinqcent
    stick/build-round.sh --dist-only --dist-hosts copland,cage,choubox ROUND   # and on the small hosts
    testvm/stictest1/drive.py --dry-run --exclude haiku6 ROUND
    testvm/stictest1/drive.py --private-copy --exclude haiku6 ROUND
    testvm/stictest1/drive.py --logs-mark st1-ROUNDb --first GUEST ROUND GUEST ...   # a recheck

- `--private-copy` runs the checks from a copy of the committed tree in
  `~/backotter-work/st1-wt/testvm/stictest1`, so a sync-hosts.sh during the
  round can't hand them newer GUEST/ scripts (testvm/compare, vm-run and
  cmp-run.sh still come from the synced `~/backotter-work`).
- Each recheck takes its own `--logs-mark`: a guest already started under a
  mark is never started again, which is also how the same command picks an
  interrupted round back up. A guest whose run left no exit file and no
  process (a host reboot) counts as LOST, so the round still ends.
- haiku6 runs only where a round has `bin/haiku`; leave it out with
  `--exclude haiku6` otherwise.
- `--self-test` checks the queue logic and how it reads the check scripts,
  the stop and skip files, and the commands it sends the hosts.
- `--stop-file F` (here, on the Mac): while F exists nothing new starts,
  and once nothing of ours runs the round ends, naming the guests not
  started. `--skip-file F`: the guests F names (whitespace-separated,
  reread every poll) are never started. SticTest2 drives its stop rules
  through these two.
- Test2's own flags, which `testvm/test2/t2 SticTest2` passes (see "Test2"
  below): `--t2-run RUN`, `--t2-copy DIR`, `--t2-cells F`, `--t2-attempt N`.

The queue times guests by their newest passing run on the hosts, else by these.

Estimates (seconds; an estimate for aeon, ~300 s (microos's 230 s and its larger ESP, docs/design/aeon.md A3), until its first run; round st2a's, GuestTest1's for the guests it added, Bazzite B5's for kinoite44, Chimera C4's for chimera, chimera-uefi and chimera-zfs, Gentoo G4's for gentoo and gentoo-systemd, AnduinOS A4's for anduinos2, PikaOS P5's for pikaos4 and pikaos4-luks, Kali K4's for kali, Guix GX5's for guix15 and guix15-bios, Proxmox P5's for proxmox9 and proxmox9-mirror, TrueNAS T5's for truenas25, Vanilla OS V6's for vanilla3, MicroOS M4's for microos, Leap 16 L4's for leap16, elementary E5's for elementary8 and elementary8-luks, an estimate for garuda until Garuda GA5's run, an estimate for biglinux until BigLinux's first run, one for hardenedbsd15-zfs until its first run (ghostbsd26-zfs's check on HBSD's slower kernel), Solus S4's for solus49, unmeasured estimates for slackware15 (devuan6's ext4 check plus LILO's /boot and pkgtools' database) and slackware15-uefi (rocky10's XFS check plus the ESP, the initrd tree and the scratch disk's layout) until Slackware S5's runs, and an unmeasured one for uc24 (vanilla3's half: four roots with no LVM or overlays, plus the LUKS2 headers and the guest's snaps) until Ubuntu Core U5's runs, one for qubes43 until its first run (Qubes Q6: dom0's ~5.5 GiB root read offline, both tools, four restores), and ZFSBootMenu Z6's for void-zbm; each arm64 guest's (docs/design/arm64.md A6) is its in-guest part's sibling's, alpine324's, ubuntu26-uefi's, fedora44-uefi's or debian13-zfs's, until its first run on Bigfoot):
aeon 300, aerynos 150, alpine324 153, alpine324-arm64 153, alpinebsd 401, anduinos2 400, arch 137, artix-dinit 170,
artix-s6 367, biglinux 300, cachyos 300, chimera 215, chimera-uefi 200, chimera-zfs 290,
debian12 117, debian13 477, debian13-arm64 374, debian13-stack 547, debian13-zfs 856, debian13-zfs-arm64 856,
devuan6 310, dragonfly6 435, elementary8 411, elementary8-luks 458, fedora44 208, fedora44-arm64 268, fedora44-uefi 268, freebsd14 751,
freebsd15 751, freebsd15-geli 800, freebsd15-ufs 391, ghostbsd26 1211,
garuda 300, gentoo 270, gentoo-systemd 230, ghostbsd26-zfs 751, guix15 470, guix15-bios 475, hardenedbsd15 519, hardenedbsd15-zfs 780, kali 330, kinoite44 404, leap16 250, microos 230, mint22 814, mx25 685, netbsd11 324,
nixos2605 236, oi2610 490, omarchy 302, omnios58 370, openbsd8 338, pikaos4 300,
pikaos4-luks 400, pop24 622, proxmox9 460, proxmox9-mirror 560, qubes43 900,
rocky10 113, rocky10-lvm 151, sculpt2610 49, slackware15 330, slackware15-uefi 250, solus49 410, truenas25 1200, tumbleweed 186, ubuntu24 957, ubuntu26-arm64 374, ubuntu26-uefi 374,
ubuntu26-zfs 957, ubuntu26-zfsmirror 623, uc24 700, vanilla3 1400, void-musl 115, void-zbm 310, zorin18 586.

## The arm64 guests (Bigfoot)

Five checks run only on an aarch64 host, Bigfoot (docs/design/arm64.md, A6):
`debian13-arm64`, `ubuntu26-arm64`, `fedora44-arm64`, `alpine324-arm64` and
`debian13-zfs-arm64`. Each check's own directory holds only its `check.sh`.
Its in-guest part is the amd64 guest's with the same layout:

| check | in-guest part |
|---|---|
| `debian13-arm64` | `../ubuntu26-uefi` |
| `ubuntu26-arm64` | `../ubuntu26-uefi` |
| `fedora44-arm64` | `../fedora44-uefi` |
| `alpine324-arm64` | `../alpine324/check-inner.sh` |
| `debian13-zfs-arm64` | `../debian13-zfs/real.sh` |

- Each check takes `BIN_DIR` `roundN/bin/linux-arm64`, or bko-bin's
  `linux-arm64`, and Bigfoot's ports 3280-3299.
- Each refuses another host, and FAILs if the guest doesn't say aarch64.
- debian13-zfs-arm64 keeps its overlay of about 12 GiB on
  `/bkoscratch/st1-scratch` (`ST1_SCRATCH`), under `--reserve 1 5`.
  Bigfoot's RAM disk is 10 GiB.

By hand, on Bigfoot:

    ~/backotter-work/testvm/stictest1/alpine324-arm64/check.sh ~/backotter-work/bko-bin/linux-arm64 3290 a6by

Or all five from the Mac, with the rest of A6's tests:
`testvm/arm64/a6-run.sh --only stic` (docs/design/arm64.md, "What A6
built"). A drive.py round that names no guests leaves them out unless
`--hosts` has bigfoot.

## BKO_TESTVM: checks from another testvm/

Every check, and the host-side helpers haiku6/guest-lib.sh and
omarchy/om-boot.sh, reads the synced tree's files as
`${BKO_TESTVM:-$WORK/testvm}/...` (or `$W`'s or `$HOME/backotter-work`'s
equivalent): vm-lib.sh, compare/ (guest-slot.sh, fsmeta.py, rt-common.sh,
torture.sh, verdict.py, run-guest-rt.sh, nonroot-rt.sh, OS/roundtrip.sh ...),
vm-run, haiku/attrs.py and omarchy/type-luks-passphrase.py. Unset, they read
`~/backotter-work/testvm` as before, byte for byte. Set to a private copy's
testvm/ (`drive.py --t2-copy DIR` sets `BKO_TESTVM=DIR/testvm` and
`BKO_WORK=~/backotter-work`), a check and everything it runs come from that
copy; the work dir still gives the guests, the logs and `cmp-run.sh` (the
queue every session shares). sculpt2610's root step passes BKO_TESTVM on
through its `sudo env`; cmp-run.sh's `sudo -E` keeps it for the rest.
`testvm/test2/stic/check-manifests.py --lint` fails on any other reference
to the synced tree.

## Test2: SticTest2, the rows and the manifests

`testvm/test2/t2 SticTest2 [ROUND] [GUEST...] [stale] [plan]` and
`SticTest2All` (testvm/test2/README.md) run the due checks of this
directory through drive.py, from the run's private copy, and record each
result in the Test2 ledger.

**Rows.** As drive.py sees a guest's exit file (or marks it LOST), and once
more at the end for any still missing, it has the host run
`t2-host row stic --mark MARK --guest GUEST --bin BIN --load N --waits S
--attempt A --checks DIR [--run RUN --cells CELLS] [--lost]` (the copy's
testvm/test2/t2-host, else `~/backotter-work/testvm/test2/t2-host`, else
none, with a warning in driver.log). The row (origin `stic:MARK:GUEST:A`,
so a repeat appends nothing) goes into that host's ledger,
`/bkoscratch/test2/ledger/HOST.jsonl` (`T2_HOST_ROOT`, `T2_LEDGER`; drive.py
passes those on when they are set). t2lib/rows_stic.py builds it from the
check's own PASS/FAIL/LIMIT lines in run.out (what results.tsv counts), the
run.out times (gm = (wall - waits) / 60), the run's cells.json, and the
inputs measured on the host from the tree the check ran from. Its verdict:
PASS when nothing FAILs and every LIMIT is classed, FAIL for a FAIL line or
a LIMIT not classed (LIMIT-NEW), HARNESS when only boot, copy, binaries,
check ran, harness or guest boots FAILs (or the check exited non-zero with
no FAIL line), LOST with no exit file; on attempt 2 a PASS is a FLAKY-FAIL,
or PASS (known-flake) when attempt 1's FAILs matched testvm/test2/stic/
flakes.json. SticTest1 rounds (no `--t2-*`) write rows too, rule `test1`:
testvm/stictest1 from the tree the checks ran from (the synced one, or
`--private-copy`'s `~/backotter-work/st1-wt`, whose commit drive.py records
in `~/backotter-work/st1-wt/COMMIT`) and the rest of testvm from the synced
tree; such a row counts as a PASS only when the synced stamp is clean and,
with `--private-copy`, the same commit. A failed write changes nothing else:
results.tsv, fails.txt, the summary and the exit code are as before.

A row is measured when it is written, which can be long after its check
began: a mark run again adopts the checks that ended then. So rows_stic
looks at every file the row's inputs stand for (the tree's files, the
binaries with their digests and SHA256SUMS, the guest's image-state and
files, a disk-keyed guest's disks, the host inputs), and one modified after
run.out.start makes the row `valid: false` (it never counts, and its note
names the files). A Test2 row is also invalid without its cells.json entry
or its planned key.

**A SticTest2 run** exits 0 only when every check it had to run, and every
rerun and fan-out, ended in a valid PASS (an attempt-2 HARNESS or LOST
leaves attempt 1's FAIL standing); 1 when any ended otherwise, had no row,
or a stop rule fired (one need failing on 2 guests of an OS, 2 harness
results, a failed slow gate, swap-out over 1 GiB on a host since the run
began, or the run's own wear-tagged writes over 2 x its budget); 2 when it
was refused (the archiver gate can't be loaded or planned, or drive.py
ended non-zero having started nothing). Before cells.json it has the copy's
`t2-host imgsum` sum the disks of a due guest with no image-state
(sculpt2610), so that guest's rows have an identity. `t2 resume RUN` waits
for a drive.py still running on the round's --out rather than start a
second, and counts the rows already fed in its stop rules again.

**What a check's key covers** is its entry in
`testvm/test2/stic/manifests.json`: its OS binaries (haiku6 also Linux's
restic-bko), its own directory (`testvm/stictest1/GUEST/**`) and every repo
file outside it that its scripts use (harness), what reads the records
(judge.py, verdict.py, expected-coverage.json) and its
`testvm/test2/stic/limits.json` entry (judge), the guest's generation and
files, alpinebsd's netbsd11 and ~/backotter-work/netbsd-tools, and for the
host-side checks (sculpt2610, haiku6, artix-dinit, artix-s6, arch) the host's
versions. vm-run, vm-lib.sh, guest-slot.sh, console-boot.py, drive.py,
cmp-run.sh and iolog.sh are the family's plumbing: a change there runs the
canaries first. After changing a check, run
`testvm/test2/stic/check-manifests.py`: it follows each check's scripts
(sources, the scripts they run or copy, Python imports) and names any repo
file one uses that its entry lacks.

**LIMITs** a check reports are classed in `testvm/test2/stic/limits.json`
(`{GUEST: {NEED: why}}`, seeded 2026-11-08 from each check's newest passing
st1-rel4* run.out on Chopin and Cinqcent: 154 needs). A LIMIT need that is
neither there nor in the check's last pass makes the result FAIL until it
is added.
