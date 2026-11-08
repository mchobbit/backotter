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
vm-run and cmp-run.sh still come from main's synced `~/backotter-work`.

| Host | Guests |
|---|---|
| Chopin | nixos2605, dragonfly6, ghostbsd26, ghostbsd26-zfs, hardenedbsd15, ubuntu26-zfs, ubuntu26-uefi, ubuntu26-zfsmirror, ubuntu24, debian12, devuan6, zorin18, mint22, debian13, pop24, mx25, sculpt2610 |
| Cinqcent | aerynos, alpine324, alpinebsd, freebsd14, freebsd15-geli, arch, artix-dinit, artix-s6, cachyos, debian13-stack, debian13-zfs, fedora44, fedora44-uefi, freebsd15, freebsd15-ufs, haiku6, netbsd11, omarchy, omnios58, openbsd8, rocky10, rocky10-lvm, tumbleweed, void-musl |

## Running one

    ~/backotter-work/testvm/stictest1/GUEST/check.sh BIN_DIR PORT PREFIX [NEED_FILTER]

- `BIN_DIR` holds restic-bko and rustic-bko for the guest's OS, for example
  `~/backotter-work/round22/bin/linux` (or freebsd, netbsd, openbsd, omnios,
  dragonfly, haiku).
- `PORT` is a free SSH port in your own range: Chopin 2700-2799, Cinqcent
  2900-2999.
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
from one queue for Chopin and Cinqcent: longest guest first, each started the
moment a host has a place, a guest slot, the guest's --reserve room after
cmp-run.sh's waiting line, our free ports (Chopin 2780-2799, Cinqcent
2980-2999) and Tctl at most 90 C. Starts are detached on the host and
watched through their exit files every 15 s; the results land in
`~/backotter-work/stictest1/MARK/results.tsv` on the Mac. See `drive.py
--help`.

    stick/build-round.sh ROUND                          # roundROUND/bin on both hosts
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
- `--self-test` checks the queue logic and how it reads the check scripts.

The queue times guests by their newest passing run on the hosts, else by these.

Estimates (seconds; round st2a's, GuestTest1's for the guests it added):
aerynos 150, alpine324 153, alpinebsd 401, arch 137, artix-dinit 170,
artix-s6 367, cachyos 300, debian12 117, debian13 477, debian13-stack 547, debian13-zfs 856,
devuan6 310, dragonfly6 435, fedora44 208, fedora44-uefi 268, freebsd14 751,
freebsd15 751, freebsd15-geli 800, freebsd15-ufs 391, ghostbsd26 1211,
ghostbsd26-zfs 751, hardenedbsd15 519, mint22 814, mx25 685, netbsd11 324,
nixos2605 236, omarchy 302, omnios58 370, openbsd8 338, pop24 622,
rocky10 113, rocky10-lvm 151, sculpt2610 49, tumbleweed 186, ubuntu24 957, ubuntu26-uefi 374,
ubuntu26-zfs 957, ubuntu26-zfsmirror 623, void-musl 115, zorin18 586.
