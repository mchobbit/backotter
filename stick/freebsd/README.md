# The FreeBSD rescue image

`../make-img-bsd.sh freebsd` makes `backotter-live-freebsd-VERSION-amd64.img`,
a remaster of FreeBSD's official memstick (15.1-RELEASE), built in the
long-lived FreeBSD builder VM (`vms/freebsd15-builder`, already running on
SSH port 2400; see `testvm/freebsd/builder-setup.sh`).  Write it raw to a USB
stick.  It boots with BIOS or UEFI, but not with Secure Boot on: FreeBSD's
loader isn't signed.

```
./make-img-bsd.sh [--test] freebsd [OUT_IMG]
./boot-test.py IMG bios --platform freebsd
./boot-test.py IMG uefi --platform freebsd
```

## Files

- `platform.conf`: the release, the builder VM, and `fetch`, which downloads
  the memstick and the release announcement into `~/backotter-work/img-media/freebsd/`
  and checks the memstick's SHA256 against the announcement, signed by the
  release engineer (FreeBSD publishes no signed checksum file; the same check
  as `testvm/make-freebsd.sh`'s).
- `build.sh`: runs in the builder.  `info` reads the kernel version
  (`freebsd-version -k`), the OpenZFS version (from `zfs.ko`) and its feature
  list (`/usr/share/zfs/compatibility.d/openzfs-X.Y`) from the memstick;
  `assemble` makes the image.
- `checks.sh`, `config-checks.sh`: `boot-test.py --platform freebsd`'s checks.

## The image

| MBR slice | Contents |
|-----------|----------|
| 1 | The memstick's ESP, as is (`EFI/BOOT/bootx64.efi` is `loader.efi`) |
| 2 | Active.  BSD label; `a` is the root, UFS2 labelled `BACKOTTER-xxxxxxxx` |
| 3 | The config partition: FAT16 (type 0x06) `BACKOTTER`, 32 MiB |

The root UFS label ends in the first 8 hex digits of the image ID, so neither
another BackOtter stick nor a FreeBSD installer stick in the same machine can
stand in for the root (the memstick's label is `FreeBSD_Install`).  `/etc/fstab`
names it, and the loader takes the root from there.  BIOS boot goes through
the MBR boot code, then `boot1`/`boot2` in the BSD label, as on the memstick.

What changes from the memstick:

- The installer is gone: `/etc/rc.local` (which starts it) and
  `/usr/freebsd-packages` (the 750 MB of packages it installs, as pkgbase).
- The root is read-only, as on the memstick (`ro` in fstab,
  `root_rw_mount="NO"`).  `/var` and `/tmp` are tmpfs through rc.conf's
  `varmfs`/`tmpmfs` (on by themselves for a read-only root); `/etc`, `/root`
  and `/home` are tmpfs through FreeBSD's own diskless setup: `/etc/diskless`
  makes `/etc/rc` run `/etc/rc.initdiskless`, which mounts a memory file
  system on each `/DIR` that has a `/conf/base/DIR` and copies that into it.
  `/conf/base/etc` is the image's `/etc` hard-linked, so it costs no space.
  The sizes are caps (tmpfs takes memory only as files fill it): `/tmp` and
  `/root` 1 GiB (restic's and rustic's caches live under `/root/.cache`),
  `/var` 256 MiB, `/home` 64 MiB, `/etc` 20 MiB.
- `rc.conf.local`: host name `backotter`, DHCP on every interface
  (`ifconfig_DEFAULT`, in the background, as on Linux), sshd off,
  sendmail off, no dump device.  ZFS stays off (`zfs_enable` is unset), so no
  pool is imported.
- `loader.conf`: the console on the screen and the first serial port both
  (`boot_multicons`, `comconsole` at 115200), for IPMI serial-over-LAN; a
  10-second menu (3 for `--test`).  The memstick's "Installer" branding is
  gone.
- getty runs BackOtter's console (`/usr/local/lib/backotter/console`, from
  `../bsd/`) as root on `ttyv0` and the serial console (`ttyu0`-`ttyu3`, on
  whichever is the console): `gettytab` entries `backotter` and
  `backotter.serial` with `al=root` and `lo=`, as agetty's `--autologin
  --login-program` on Linux.  `ttyv1`-`ttyv7` log root straight into a shell
  (`al.Pc`), like Linux's tty2-tty6.
- sshd: `../bsd/sshd_config.backotter`'s lines first in `sshd_config`; no
  host keys (`remote-access` makes them).
- `/usr/local/bin/restic-bko` and `rustic-bko`, and the marker
  `/.disk/backotter.json`.

## Notes

- **QEMU's USB stick on xHCI.**  FreeBSD 15.1 can't read QEMU 10's emulated
  USB stick (`usb-storage` on `qemu-xhci` or `nec-usb-xhci`) with its default
  1 MiB maximum I/O: reads bigger than one USB transfer come back garbled
  (umass: "bad CSW signature"), and the root never mounts.  The stock memstick
  hangs the same way, and 128 KiB isn't small enough.  `--test` images
  set `kern.maxphys="65536"` for `boot-test.py`; the image for real sticks
  keeps the default.  EHCI works without it.
- **Typing into the serial console.**  FreeBSD's `sh` edits lines (libedit)
  on a terminal and mangles long lines typed fast, and the serial driver
  holds only 384 bytes while the system is busy, dropping the rest.
  `checks.sh` turns line editing off first and keeps each line under 300
  characters, putting longer logic in scripts it writes with here-documents.
- **No Python, no TOML parser** in the base system.  The checks read JSON
  with `flua` (FreeBSD's Lua, with libucl) and only grep `settings.toml`'s
  format and schema lines; the Linux image's check parses it.
- On the serial console root's `.profile` runs `resizewin`, which waits a
  second for a terminal that doesn't answer ("resizewin: timeout reading from
  terminal").  Harmless.
