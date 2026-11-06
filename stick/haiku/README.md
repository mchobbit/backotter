# The Haiku rescue image

`../make-img-bsd.sh haiku` makes `backotter-live-haiku-VERSION-amd64.img` from
Haiku's official anyboot image (R1/beta6), built in a throwaway `--snapshot`
copy of the `haiku6` test guest (`testvm/make-haiku.sh`).  Its packages are
updated: each of the anyboot image's is replaced by the guest's version where
that differs, and testvm's vm-update keeps the guest up to date from Haiku's
r1beta6 repository (`pkgman full-sync`), so the image gets the guest's last
update.  An updated `haiku_loader` package brings the guest's
`haiku_loader.efi` to the ESP as well.  Write it raw to a
USB stick.  It boots with BIOS or UEFI, but not with Secure Boot on: Haiku's
`haiku_loader.efi` isn't signed.

```
./make-img-bsd.sh haiku [OUT_IMG]
./boot-test.py IMG bios --platform haiku
./boot-test.py IMG uefi --platform haiku
```

It carries `rustic-bko` only: restic isn't ported to Haiku (Haiku's Go is
1.18, restic needs 1.25), so `platform.conf` sets `RESTIC_REQUIRED=no`.

## Files

- `platform.conf`: the release, the build VM (logged in as `user`, Haiku's
  uid 0), and `fetch`, which puts `haiku-r1beta6-x86_64-anyboot.iso` and its
  `.minisig` in `~/backotter-work/img-media/haiku/` (symlinks to the `haiku6`
  guest's copies where it has them) and checks the image with minisign
  against Haiku's signing key, as `make-haiku.sh` does.
- `build.sh`: runs in the VM (bash).  `info` reads the hrev from the official
  image's `haiku` package; `assemble` makes the image.
- `mbr.py`: writes the image's MBR partition table (Haiku has no
  command-line partitioner; the VM has Python).
- `console`, `remote-access`: Haiku's own versions of `stick/bsd`'s, which
  they replace in the build (`BUILD_FILES`).
- `boot`, `on-serial.py`: BackOtter's boot job and its serial console
  (below).
- `checks.sh`, `config-checks.sh`: `boot-test.py --platform haiku`'s checks.
- `build-rustic.sh`: builds `rustic-bko` natively in a `haiku6` copy, from
  the pushed fork or local trees (`SRC=`); `../build-round.sh` runs it on
  Chopin in every round, so each round's binaries include Haiku's.

## The image

| MBR partition | Contents |
|---|---|
| 1 | Type 0xEB, active: BFS `BackOtter`, the system |
| 2 | Type 0xEF: the official image's EFI system partition, as is (`EFI/BOOT/BOOTX64.EFI` is `haiku_loader.efi`) |
| 3 | Type 0x0E: the config partition, FAT16 `BACKOTTER`, 32 MiB |

The MBR's boot code is `writembr`'s, partition 1's boot block
`makebootable`'s, as Haiku's Installer leaves a disk.  Partition 1 holds the
official image's `system` and `home`, copied with their attributes
(`copyattr`), without the install media's `_packages_` and `_sources_`, with
the updated packages in `system/packages`, and
cut to its contents plus 256 MB.  The official image's ISO9660 part (for CDs)
isn't kept: the image is for USB sticks.

The system is **read-write**, unlike the other images'.  Haiku puts a write
overlay over its boot volume only on read-only media (CDs; see
`vfs_boot.cpp`), and a USB stick isn't one, so Haiku's own live USB sticks are
read-write too.  The system's files are read-only all the same (packagefs);
what Haiku writes is settings, logs and caches.  What BackOtter adds at run
time, the boot job forgets at the next boot.

What changes from the official image:

- Host name `backotter`; DHCP on every interface (net_server's default).
- sshd doesn't start at boot: `network/services` no longer lists it.
  `stick/bsd/sshd_config.backotter`'s lines come first in `sshd_config`, and
  there are no host keys.
- `remote-access` creates `backotter` (home in `/boot/system/var`) and gives
  it a one-time password, and `user`, Haiku's uid 0, the same one for `su`.
  Haiku's registrar keeps the users, so the passwords go through `passwd`, on
  a pseudo-terminal (Python's `pty`): editing `/etc/shadow` doesn't reach the
  registrar.  sshd refuses `user` (`PermitRootLogin no`, `AllowUsers
  backotter`); Haiku has no other uid 0 to refuse.
- The boot job: `system/settings/launch/backotter` has launch_daemon run
  `boot` at every boot, once the registrar is up.  It deletes `backotter`,
  `user`'s password, the host keys and the console's note (and stamps
  `/tmp/backotter-booted`), then runs the
  console on the first serial port (`/dev/ports/pc_serial0`, 115200 baud),
  again whenever it exits, as getty does on the BSD images.  Haiku's serial
  ports stay raw (`tcsetattr` succeeds and changes nothing: no echo, no line
  editing, no ^C, no CR/LF translation) and can be open only once at a time,
  so `on-serial.py` runs the console on a pseudo-terminal and copies bytes
  between it and the port.  The kernel's log stays off the serial port
  (`serial_debug_output false`, Haiku's default): `pc_serial` leaves out the
  port the kernel uses.
- The stick's first boot makes sshd host keys anyway: package_daemon runs
  every package's first-boot script once, openssh's `sshd_keymaker.sh` among
  them (and the one that creates the `sshd` user, so first-boot processing
  stays).  Those keys are that boot's own, unique to the stick, and the next
  boot's reset deletes them; `sshd-off-at-boot` checks that no host key is
  older than the boot job's stamp.
- On the desktop, the UserBootscript opens the console in a Terminal window
  titled `BackOtter`.  The welcome window (language, keymap, "Install
  Haiku") would come first on a writable boot volume without `Locale
  settings`, so the build copies that and `Key_map` from the build VM, as
  `testvm/haiku/install.sh` copies them into the test guest (English,
  US-International: "Try Haiku"'s defaults).  Haiku has no virtual
  consoles: its desktop is the screen.
- Every shell's prompt ends in `# ` (`non-packaged/data/profile.d`): every
  process on Haiku is uid 0's.
- `rustic-bko` in `/boot/system/non-packaged/bin`, from
  `~/backotter-work/bko-bin/haiku/` (`BKO_BIN`), `bko-holt` beside it from
  `~/backotter-work/holt-bin/haiku/` (`BKO_HOLT_BIN`, a pinned set of holt's builds), the marker in
  `/boot/.disk/backotter.json` (mode 444).  `zfs` is null: Haiku has no ZFS.
  `kernel` is the hrev, as the first word of `uname -v` says it
  (`hrev59866+79`).

## Testing in QEMU

`boot-test.py --platform haiku` boots it on q35 with an xHCI stick, as the
Linux image, talks to the console on the serial port, and powers off with
`shutdown -q`.  Its `boot-reset` check runs the boot job's reset by hand
after `remote-access`, since the test boots only once.
