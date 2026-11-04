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
| Chopin | nixos2605, dragonfly6, ubuntu26-uefi, ubuntu26-zfsmirror, ubuntu24, debian12, devuan6, zorin18, mint22, debian13, pop24, mx25, sculpt2610 |
| Cinqcent | alpine324, alpinebsd, arch, artix-dinit, artix-s6, cachyos, debian13-zfs, fedora44, fedora44-uefi, freebsd15, freebsd15-ufs, haiku6, netbsd11, omarchy, omnios58, openbsd8, rocky10, tumbleweed, void-musl |

## Running one

    ~/backotter-work/testvm/stictest1/GUEST/check.sh BIN_DIR PORT PREFIX [NEED_FILTER]

- `BIN_DIR` holds restic-bko and rustic-bko for the guest's OS, for example
  `~/backotter-work/round22/bin/linux` (or freebsd, netbsd, openbsd, omnios,
  dragonfly, haiku).
- `PORT` is a free SSH port in your own range: Chopin 2700-2799, Cinqcent
  2900-2999.
- `PREFIX` names your scratch: `/mnt/bko-ram/PREFIX`, `/bkoscratch/PREFIX`.

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
- Most guests finish in 2 to 15 minutes.

A recheck or a regression run is the same command with a newer round's
BIN_DIR. The round 22 driver used for the final regression is
`~/backotter-work/stictest1/final-r22/drive.sh` on each host; it runs the old
copies.
