# Archiver comparisons and round trips

What restic, BackOtter's restic (restic-bko) and BackOtter's rustic
(rustic-bko) keep of a file's metadata, checked on the test guests that
`testvm/make-*.sh` make, on the KVM test hosts.

## Scripts

Here, in `testvm/compare/`:

| Script | What it does |
|---|---|
| `compare.py` | Back up and restore a Linux guest's whole disk with each tool, and compare (see its header) |
| `fs_roundtrip.py` | Round trip of `torture.sh`'s files on one kind of Linux file system, made on the host with every feature they need (bcachefs only where there is a `mkfs.bcachefs`; `zfs-ds`: file systems in an existing pool); `torture.sh`'s socket must be in neither tool's snapshot and absent after every restore, unmentioned, exit 0 (both tools skip sockets, SPEC3 1) |
| `zfs-guest-rt.sh GUEST [PORT] [FSTYPE...]` | `fs_roundtrip.py`'s ZFS round trips (`zfs`, `zfs-ds`) inside a throwaway copy of ubuntu24 or ubuntu26-zfsmirror (encrypted, mirrored root pool; it types the passphrase), with the guest's own ZFS: DOS flags, xattrs, ACLs, project IDs; and `sparse-rt.sh` in a directory of the root pool (`sparse`; with `OLD_RESTIC` and `OLD_RUSTIC`, the round before's binaries, SPEC3 2's snapshot identity too) |
| `torture.sh`, `fsmeta.py` | Hard-to-preserve files, and recording and comparing metadata, for the Linux checks |
| `sparse-rt.sh RESTIC RUSTIC [OLD_RESTIC OLD_RUSTIC]` | Sparse files of known layouts (holes at the start, middle and end, data at EOF, 4 KiB and 64 KiB holes, all hole, all data, written zeros, a `posix_fallocate`d file, unwritten and not marked, and a 20 GiB file with 200 MiB of data in 100 extents) backed up and restored in all four directions; per file: the contents, the unix.sparse mark against S3, st_blocks against SPEC3 3 as revised (a marked file: no more than the source's where its holes are B-aligned, never more than source + one block per blob, both plus the metadata the restored file has beyond the source's, e.g. XFS extent-tree blocks, reported; an unmarked one: dense, not sparse by S3's own rule), the SEEK_DATA/SEEK_HOLE maps; the bytes read (`/proc/PID/io` rchar and read_bytes) and wall time of each backup, the wall time of each restore and the sync after it; a socket in the tree, which must be in no snapshot, absent after every restore, unmentioned (SPEC3 1); with the OLD binaries, SPEC3 2's snapshot identity (every file's content blob IDs the same as the old build's, in one repository), and their restores of the new snapshots beside the new ones (st_blocks and times, not a check).  On Linux as root on a file system it makes (`SPARSE_FS`: ext4, xfs, btrfs, zfs with `SPARSE_ZFS_OPTS` for `zpool create`, tmpfs); in a guest in a directory (below) |
| `compare-freebsd.sh` | Whole-system backup and restore of a FreeBSD guest with restic-bko, and a boot of the result: `compare.py`'s FreeBSD counterpart |
| `OS/roundtrip.sh` | A round trip inside a guest of that OS: `freebsd/` (also NFSv4 ACLs on a UFS md disk, and a restore onto a UFS without ACLs), `openbsd/` (also a socket, which neither tool keeps), `netbsd/` (also AlpineBSD's root extended attributes, FFSv1, restores onto FFSv1 and plain FFSv2, and a socket, which neither tool keeps), `dragonfly/` (also a socket, which neither tool keeps), `illumos/` (also av_quarantined, a restore with idmap unreachable, and a socket, which neither tool keeps); each also restores over a changed copy of what it restored, and, run with both tools, compares their snapshots node by node (`rt-common.sh`) |
| `run-guest-rt.sh GUEST [PORT] RESTIC RUSTIC [DIRECTION...]` | Runs `OS/roundtrip.sh` in throwaway copies of GUEST, in all four directions (restic->restic, rustic->rustic, and across), and prints PASS or FAIL with the failed checks |
| `nonroot-rt.sh linux` or `GUEST [PORT]` | Restores as an unprivileged user, on the host (ext4 and ZFS) or in freebsd15, freebsd15-ufs, netbsd11, alpinebsd, dragonfly6, openbsd8 or omnios58 (`nonroot-inner.sh` is the part that runs there) |
| `guest-run.sh GUEST [PORT] COMMAND [FILE...]` | A one-off command in a throwaway copy of a guest |
| `rt-common.sh` | What the guests' round trips, `nonroot-inner.sh` and `sparse-rt.sh` share: a snapshot's nodes decoded (with restic's `cat blob`; `RT_CONTENT` keeps the content blob IDs; scratch files in `RT_TMP`), two snapshots compared, a restored tree changed for a restore over it, a socket made (`rt_mksock`: python3, perl, cc or `nc -lU`) |

In `stick/`, the builds of rustic-bko, which tests and images share:

| Script | What it does |
|---|---|
| `freebsd/build-rustic.sh`, `openbsd/build-rustic.sh`, `netbsd/build-rustic.sh`, `illumos/build-rustic.sh` | Native builds in a throwaway copy of freebsd15, openbsd8, netbsd11, omnios58 (the work is in `build-rustic-native.sh`); `--setup` keeps a build image with the tools installed |
| `dragonfly/build-rustic.sh` | Cross-build for DragonFly on the host (its packaged Rust is too old) |
| `prep-rustic-src.sh` | In `SRC=` mode, every one of them points rustic at the local rustic_core (and its vendored walkdir) with it |

Each writes `$BKO_BIN/OS/rustic-bko` and `rustic-commit`.  `BKO_BIN`
defaults to `~/backotter-work/bko-bin`, the binaries the images ship: a test
round gives it its own directory.  AlpineBSD runs the NetBSD binary.
`testvm/vm-lib.sh` holds what these scripts share for driving a guest: boot,
`/bin/sh -c` around every command (DragonFly root's shell is csh), files over
`ssh cat` (AlpineBSD has no scp), poweroff by full path (NetBSD root's PATH
has no /sbin), QEMU stopped by PID, `timeout` around ssh (QEMU's user network
accepts before the guest is up), and the legacy virtio-net OmniOS needs.

## On the hosts

The hosts run these scripts from `~/backotter-work` (`BKO_WORK`), with the
guests in `~/backotter-work/vms`.  `testvm/sync-hosts.sh`, run from the
checkout, copies `testvm/` and `stick/` there on every host, so that both run
the same scripts; the local restic, rustic and rustic_core trees go across
the same way, for `SRC=` (without .git, `rustic-commit` names the commits
"unknown" unless `RUSTIC_COMMIT` and `RUSTIC_CORE_COMMIT` give them):

    testvm/sync-hosts.sh                  # chopin.local and cinqcent.local
    for h in chopin.local cinqcent.local; do
        rsync -rlp --checksum --exclude target --exclude .git \
            ~/git/restic ~/git/rustic ~/git/rustic_core m@$h:backotter-work/src/
    done

Then, on a host, for example:

    cd ~/backotter-work
    SRC=src BKO_BIN=$PWD/round4/bin FAST=1 stick/freebsd/build-rustic.sh 2901
    testvm/compare/run-guest-rt.sh freebsd15 2910 bko-bin/freebsd/restic-bko \
        round4/bin/freebsd/rustic-bko

restic-bko (all platforms) and rustic-bko for Linux are the forks' own
`build-backotter.sh`, into their `dist/`; for rustic, on a copy of the two
trees that `stick/prep-rustic-src.sh` has pointed at the local rustic_core
(on Chopin, Go is in `~/sdk/goVERSION/bin`, cargo in `~/.cargo/bin`):

    (cd src/restic && ./build-backotter.sh linux/amd64 freebsd/amd64 \
        openbsd/amd64 netbsd/amd64 dragonfly/amd64 illumos/amd64)
    mkdir -p round4/linux && cp -a src/rustic src/rustic_core round4/linux/
    stick/prep-rustic-src.sh round4/linux && (cd round4/linux/rustic && ./build-backotter.sh)

Copy restic's `dist/restic-commit` into each `$BKO_BIN/OS/` beside
`restic-bko`, as `rustic-commit` is (`RESTIC_COMMIT` names the commit for a
copy without .git).  Its source digest must match
`~/git/restic/build-backotter.sh --source-digest` on the Mac (no Go needed),
or the copy the build used was stale: round 7's Linux restic-bko was built
from a copy synced before the commit it was meant to test.

Build and test work is split between the two hosts (each takes a share of
the slow steps, native guest builds and round trips):

| Chopin | Cinqcent |
|---|---|
| restic-bko cross-builds (the restic fork's build) | native rustic-bko builds: freebsd15, openbsd8, omnios58, netbsd11 |
| rustic-bko for Linux, and `dragonfly/build-rustic.sh` | round trips on freebsd15, freebsd15-ufs, netbsd11, alpinebsd |
| `compare.py`, `fs_roundtrip.py`, `nonroot-rt.sh linux`, `sparse-rt.sh` (as root) | `nonroot-rt.sh freebsd15`, `freebsd15-ufs`, `netbsd11`, `alpinebsd` |
| round trips and `nonroot-rt.sh` on dragonfly6, openbsd8, omnios58; `zfs-guest-rt.sh` | |

The openbsd8 and omnios58 binaries are built on Cinqcent and tested on
Chopin, which can ssh to Cinqcent: copy them across from there, e.g.
`rsync -rlp m@cinqcent.local:backotter-work/round4/bin/ round4/bin/`, and
bring the logs (`~/backotter-work/rt/`, `nonroot/`, `zfs-guest/`) back to one place.  Both
hosts are shared with other work: give every guest a port of its own, and
keep off the ports others use (2400 is the long-lived freebsd15-builder's;
the image builds use 24xx).  The scripts' default ports are 2710-2713 for
the round trips (Chopin's share; 2713 is `zfs-guest-rt.sh`'s) and 2901-2903 for the native builds
(Cinqcent's); `netbsd/build-rustic.sh` keeps 2412 and
`dragonfly/build-rustic.sh` 2421, the image builds' own.  Cinqcent takes at
most three guests of ours at a time.

`sparse-rt.sh` on a host (Chopin), as root, one file system per run (each
`SPARSE_RUN`, by default the file system's name, has its own image, mount
point, pool and work directory, `/var/tmp/bko-sparse/RUN`, so runs of
different file systems can go at the same time; `SPARSE_PREFIX`, e.g.
`bkor9-sparse`, replaces `bko-sparse` in all of those names, so that a
session's runs keep apart from another's); with the old binaries (the
round before) it also checks the snapshot identity and measures before and
after.  The report is on stdout and ends in a `sparse-rt: PASS` or `FAIL`
line:

    for fs in ext4 xfs zfs; do
        SPARSE_PREFIX=bkor9-sparse SPARSE_FS=$fs testvm/compare/sparse-rt.sh round8/bin/linux/restic-bko \
            round8/bin/linux/rustic-bko round7/bin/linux/restic-bko \
            round7/bin/linux/rustic-bko >logs/sparse-$fs.log 2>&1 &
    done

In a guest, through `guest-run.sh`, it works in a directory of the guest's
own file system (`SPARSE_DIR`, default `/root/bko-sparse`): FreeBSD and
illumos have SEEK_HOLE; elsewhere the marks, contents and allocation are
checked all the same.  Its helper needs cc (FreeBSD's base has one) or
python3 (omnios58):

    C=testvm/compare B=round8/bin/freebsd
    T=7200 $C/guest-run.sh freebsd15 2780 \
        'cd /root && BIG=2 sh sparse-rt.sh /root/restic-bko /root/rustic-bko' \
        $C/sparse-rt.sh $C/rt-common.sh $B/restic-bko $B/rustic-bko

Old binaries go in under other names that still say restic or rustic
(`restic-bko-old`, `rustic-bko-old`).  `BIG` is the big file's size in GiB
(default 20, 0 for none; a small guest takes long to read 20 GiB of holes
with the old binaries).  Outside Linux only the times are measured: there
is no `/proc/PID/io`.

Run the `fs_roundtrip.py` file systems at the same time, not one after
another: they were the longest step of a round (~48 min one by one in round
6).  Each run has its own mount points, images and pools
(`BKO_FSRT_PREFIX`, e.g. `bkor9-fsrt`, names them all, and the results
directory, for a session of its own), so e.g.

    printf '%s\n' zfs ext4 xfs btrfs f2fs tmpfs bcachefs 'zfs --onto ext4' |
        xargs -P 8 -I{} sh -c 'python3 testvm/compare/fs_roundtrip.py {} >logs/$(echo {} | tr " " _).log 2>&1'
