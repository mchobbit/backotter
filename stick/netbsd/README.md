# NetBSD rescue image

`backotter-live-netbsd-VERSION-amd64.img`, made by `../make-img-bsd.sh netbsd`
from NetBSD 11.0's official sets, inside a throwaway copy of the `netbsd11` test
guest (`testvm/make-netbsd.sh 11.0 vms/netbsd11`).  It is also the image for
AlpineBSD, which runs NetBSD's kernel: the rescue system only needs the kernel to
see the machine's disks and file systems, and AlpineBSD's are NetBSD's (FFSv2,
with extended attributes; GPT wedges).

```
stick/make-img-bsd.sh [--test] netbsd [OUT_IMG]
stick/boot-test.py IMG {bios,uefi} --platform netbsd
```

## What goes in

`platform.conf`'s `fetch` downloads the sets `base etc kern-GENERIC misc modules
rescue` from cdn.netbsd.org into `~/backotter-work/img-media/netbsd/` and checks
each against the release's hash list signed by the NetBSD Security Officer (the key
fetched by full fingerprint, as `testvm/make-netbsd.sh` does).  `EXTRA_SETS=comp`
fetches more, e.g. for a build VM.  Not included: comp, games, man, tests, text, X.

`restic-bko` and `rustic-bko` come from `~/backotter-work/bko-bin/netbsd/`.
`build-rustic.sh` makes rustic-bko natively in a throwaway copy of `netbsd11`:
the comp set, then `rust` (1.96), `cmake`, `gmake`, `perl` and `pkgconf` from
pkgsrc's binary packages (`pkgsrc/packages/NetBSD/x86_64/11.0_2026Q3`), then
`cargo build --release --locked` of branch `backotter` of
github.com/mchobbit/rustic (its Cargo.lock pins rustic_core's commit), or with
`SRC=DIR` the local trees `DIR/rustic` and `DIR/rustic_core`.  In the
guest, libfetch needs `ip6addrctl_policy=ipv4_prefer` (QEMU's user network has no
IPv6 route out) and `certctl rehash` (no TLS trust anchors until then).
`build-rustic.sh --setup` makes a build image with all that installed
(`~/backotter-work/vms-bko-build/netbsd11-rust.qcow2`), which later builds
start from; the work is in `../build-rustic-native.sh`, shared with FreeBSD,
OpenBSD and OmniOS.  AlpineBSD runs the same binary: its libc is NetBSD's.

## Layout

GPT, NetBSD's own recipe (src/distrib/common/bootimage/Makefile.bootimage): the
sets extracted as root (so no mtree spec is needed), then `makefs`, `gpt` and
`installboot` on plain files, no vnd.

| # | Type                  | Contents                                           |
|---|-----------------------|----------------------------------------------------|
| 1 | EFI system, 64 MiB    | FAT32: `EFI/BOOT/BOOTX64.EFI`, `BOOTIA32.EFI`      |
| 2 | NetBSD FFS, `bootme`  | FFSv2 root, read-only, `bootxx_ffsv2` installed    |
| 3 | Microsoft basic data  | `config.img` from `marker.py`: FAT16 `BACKOTTER`   |

BIOS: the protective MBR's `gptmbr.bin` (`gpt biosboot -i 2`) loads partition 2's
`bootxx_ffsv2`, which loads `/boot`.  UEFI: `BOOTX64.EFI` finds the `bootme`
partition.  Both read `/boot.cfg` (menu: normal, console on the serial port,
single-user, boot prompt; `--test` puts the console on com0 and waits 1 s).  The
kernel finds its root wedge from the boot loader; no Secure Boot (NetBSD's loaders
aren't signed).  No first-boot resize, unlike NetBSD's own live image.

## The running system

- `/` is read-only: fstab says `ROOT. / ffs ro 0 0` (`ROOT.` is whatever
  `kern.root_device` is, so another disk with the same labels can't be picked) and
  no fsck.  NetBSD has no switch for a read-only root with writable `/etc` and
  `/var`, so `/etc/rc.d/backotter_rw` (right after `rc.d/root`, before anything
  writes) copies `/etc`, `/var`, `/root` and `/home` into one tmpfs on
  `/.backotter-rw` (at most half the memory: there's no swap) and null-mounts
  each copy over its directory.  If that fails it stops the boot (single-user):
  half of them read-only is no system to rescue with.
- `/dev` holds only `MAKEDEV`: with no `/dev/console`, init makes `/dev` a tmpfs
  and runs MAKEDEV there, as on NetBSD's install media.  `/tmp` and `/var/shm`
  are tmpfs.
- Consoles: `/etc/ttys` runs getty on `ttyE0` (screen) and `tty00` (serial,
  115200) with gettytab entries that log root in (`al=root`) and run
  `/usr/local/lib/backotter/console` as the login program (`lo=`); `ttyE1` gets a
  root shell (`al=root`, like the Linux image's tty2).  `console` and `constty`
  are off, so the kernel's console can be either.
- rc.conf: hostname `backotter`, dhcpcd in the background (`-b`: no wait for a
  lease), `entropy=""` (no wait at boot; no seed file is shipped, since every
  stick would share it), no swap, no sshd, cron, inetd, postfix, savecore.
- SSH: off until the console's "Allow remote access" (`remote-access`), which
  makes this boot's host keys and starts sshd with `/etc/rc.d/sshd onestart`.
  `sshd_config.backotter`'s lines come first in `/etc/ssh/sshd_config`; no host
  keys are shipped.
- TLS trust anchors are set up at build time (`certctl rehash`).
- Disk tools are base's: gpt, dkctl, disklabel, fdisk, newfs, fsck_ffs,
  newfs_msdos, zpool/zfs (NetBSD's own ZFS, not OpenZFS, so the marker's `zfs` is
  null), lvm, raidctl, cgdconfig; and `/rescue`.
- The marker is `/.disk/backotter.json`; its `kernel` is `uname -r` (`11.0`).

## Checks

`checks.sh` and `config-checks.sh` (for `boot-test.py --platform netbsd`) use
base tools only: there's no Python or blkid, so the config partition's FAT boot
sector is read with od, JSON with sed, and settings.toml's format and schema
lines with grep.  NetBSD's tty takes only 1 KB of typed input (`kern.tty.qsize`),
so boot-test.py waits for each check's result before typing on.
