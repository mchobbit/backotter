# Archiver comparisons and round trips

What restic, BackOtter's restic (restic-bko) and BackOtter's rustic
(rustic-bko) keep of a file's metadata, checked on the test guests that
`testvm/make-*.sh` make, on the KVM test hosts.

## Scripts

Here, in `testvm/compare/`:

| Script | What it does |
|---|---|
| `compare.py` | Back up and restore a Linux guest's whole disk with each tool, and compare (see its header) |
| `fs_roundtrip.py` | Round trip of `torture.sh`'s files on one kind of Linux file system, made on the host with every feature they need |
| `torture.sh`, `fsmeta.py` | Hard-to-preserve files, and recording and comparing metadata, for the Linux checks |
| `compare-freebsd.sh` | Whole-system backup and restore of a FreeBSD guest with restic-bko, and a boot of the result: `compare.py`'s FreeBSD counterpart |
| `OS/roundtrip.sh` | A round trip inside a guest of that OS: `freebsd/`, `openbsd/`, `netbsd/` (also AlpineBSD), `dragonfly/`, `illumos/` |
| `run-guest-rt.sh GUEST [PORT] RESTIC RUSTIC [DIRECTION...]` | Runs `OS/roundtrip.sh` in throwaway copies of GUEST, in all four directions (restic->restic, rustic->rustic, and across), and prints PASS or FAIL with the failed checks |
| `nonroot-rt.sh linux` or `GUEST [PORT]` | Restores as an unprivileged user, on the host (ext4 and ZFS) or in freebsd15 / freebsd15-ufs (`nonroot-inner.sh` is the part that runs there) |
| `guest-run.sh GUEST [PORT] COMMAND [FILE...]` | A one-off command in a throwaway copy of a guest |

In `stick/`, the builds of rustic-bko, which tests and images share:

| Script | What it does |
|---|---|
| `freebsd/build-rustic.sh`, `openbsd/build-rustic.sh`, `netbsd/build-rustic.sh`, `omnios/build-rustic.sh` | Native builds in a throwaway copy of freebsd15, openbsd8, netbsd11, omnios58 (the work is in `build-rustic-native.sh`); `--setup` keeps a build image with the tools installed |
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

Build and test work is split between the two hosts (each takes a share of
the slow steps, native guest builds and round trips):

| Chopin | Cinqcent |
|---|---|
| restic-bko cross-builds (the restic fork's build) | native rustic-bko builds: freebsd15, openbsd8, omnios58, netbsd11 |
| rustic-bko for Linux, and `dragonfly/build-rustic.sh` | round trips on freebsd15, freebsd15-ufs, netbsd11, alpinebsd |
| `compare.py`, `fs_roundtrip.py`, `nonroot-rt.sh linux` (as root) | `nonroot-rt.sh freebsd15`, `freebsd15-ufs` |
| round trips on dragonfly6, openbsd8, omnios58 | |

The openbsd8 and omnios58 binaries are built on Cinqcent and tested on
Chopin, which can ssh to Cinqcent: copy them across from there, e.g.
`rsync -rlp m@cinqcent.local:backotter-work/round4/bin/ round4/bin/`, and
bring the logs (`~/backotter-work/rt/`, `nonroot/`) back to one place.  Both
hosts are shared with other work: give every guest a port of its own, and
keep off the ports others use (2400 is the long-lived freebsd15-builder's;
the image builds use 24xx).  The scripts' default ports are 2710-2712 for
the round trips (Chopin's share) and 2901-2903 for the native builds
(Cinqcent's); `netbsd/build-rustic.sh` keeps 2412 and
`dragonfly/build-rustic.sh` 2421, the image builds' own.  Cinqcent takes at
most three guests of ours at a time.
