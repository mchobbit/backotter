# The farm on trixie-backports (2026-11-14)

All four amd64 hosts (Chopin, Cinqcent, Cage, Copland) take their kernel, ZFS,
QEMU and firmware from Debian `trixie-backports`. The rest of the system is
plain Debian 13.

## Sources and pins

- `/etc/apt/sources.list.d/trixie-backports.list`, the same line on all four:
  `deb http://deb.debian.org/debian trixie-backports main contrib non-free non-free-firmware`.
- No pins. Backports keep their default priority, 100, so a package comes
  from them only when it is named with `-t trixie-backports`. A plain
  `apt upgrade` keeps upgrading what came from there.
- `/etc/apt/sources.list.d/apt.bcachefs.org.sources` and
  `/etc/apt/keyrings/apt.bcachefs.org.asc` (signing subkey
  EA483B991020C72A8A5035ADA0620B5E0E01C1DD) provide `bcachefs-tools` and
  `bcachefs-kernel-dkms`. Kernel 7.x has no in-tree bcachefs, and the gate's
  root tests (`TestBcachefs*`, `TestNodeProjectIDBcachefs`) mount bcachefs on
  the host.

## What came from backports

These were installed with
`apt-get install -t trixie-backports`: `linux-image-amd64 linux-headers-amd64
linux-sysctl-defaults zfs-dkms zfsutils-linux` (plus `zfs-zed` on Chopin and
Cinqcent) `btrfs-progs`, every installed `qemu-*` package, `seabios`, and every
installed `firmware-*` package except `firmware-ipw2x00` and `firmware-ivtv`
(their licence prompts). The bcachefs repository supplied `bcachefs-tools` and
`bcachefs-kernel-dkms`.

Left on trixie on purpose: OVMF and swtpm (backports has none), systemd,
xfsprogs and e2fsprogs (none either), openssh (10.3 in backports: the farm
runs on SSH, and trixie's gets security fixes), python3-pytest 9 (Test2),
tmux, curl and the desktop stacks (LibreOffice, Mesa and the like).

The 6.12 kernel stays installed as a fallback. ZFS 2.4.4 DKMS is built for it
too.

## Before and after

| Host | Before: kernel / ZFS userland, kmod / QEMU | After: kernel / ZFS userland, kmod / QEMU / bcachefs | Rebooted |
|---|---|---|---|
| Chopin | 6.12.116 / 2.3.9, 2.3.9 / 10.0.13 | 7.2.12 / 2.4.4, 2.4.4 / 11.1.2 / 1.39.7 DKMS | 18:37 |
| Cinqcent | 6.12.115 / 2.3.9, 2.3.9 / 10.0.13 | 7.2.12 / 2.4.4, 2.4.4 / 11.1.2 / 1.39.7 DKMS | 18:26 |
| Cage | 6.12.116 / 2.4.4, 2.3.9 (installed, not rebooted) / 11.1.2 | 7.2.12 / 2.4.4, 2.4.4 / 11.1.2 / 1.39.7 DKMS | 18:17, up 18:32 |
| Copland | 6.12.116 / 2.4.4, 2.3.9 (installed, not rebooted) / 11.1.2 | 7.2.12 / 2.4.4, 2.4.4 / 11.1.2 / 1.39.7 DKMS | 18:17 |

The other versions went the same way on all four:

| Component | Before | After |
|---|---|---|
| SeaBIOS | 1.16.3 | 1.17.0 |
| btrfs-progs | 6.14 | 7.3 |
| firmware-nonfree | 20250410 | 20260916 |

OVMF stays at 2025.02-8+deb13u1. Secure Boot is off on all four, so the
unsigned DKMS modules load.

## What this changes for tests

- **Saved QEMU states.** BackShot2's rescuer-state key includes QEMU, OVMF and
  the host kernel, so each host saves one fresh state on its first case.
- **Host-made ZFS pools now get 2.4 features.** `make-debian-zfs.sh` and
  `make-chimera-zfs.sh` run `zpool create` on the host with every feature its
  OpenZFS has. On a 2.4.4 host, a rebuilt guest pool may carry features a
  guest's older ZFS (trixie's 2.3.9) can't import read-write.
  `make-void-zfs.sh` pins `-o compatibility=openzfs-2.3-linux` and is safe.
- **ARC.** Chopin and Cinqcent keep `zfs_arc_max` 2 GiB
  (`/etc/modprobe.d/zfs-arc.conf`).
- **Logs.** The upgrade log is `/root/bko-backports-upgrade.log` on Chopin and
  Cinqcent.
