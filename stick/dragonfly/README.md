# The DragonFly BSD rescue image

`../make-img-bsd.sh dragonfly` makes `backotter-live-dragonfly-VERSION-amd64.img`
from DragonFly's official USB image (6.4.2, still the newest release), built
in a throwaway `--snapshot` copy of the `dragonfly6` test guest
(`testvm/make-dragonfly.sh`).  Write it raw to a USB stick.  It boots with
BIOS or UEFI, but not with Secure Boot on: DragonFly's `boot1.efi` isn't
signed.

```
./make-img-bsd.sh [--test] dragonfly [OUT_IMG]
./boot-test.py IMG bios --platform dragonfly
./boot-test.py IMG uefi --platform dragonfly
```

## Files

- `platform.conf`: the release, the build VM, and `fetch`, which downloads
  `dfly-x86_64-6.4.2_REL.img.bz2` and the mirror's `md5.txt` into
  `~/backotter-work/img-media/dragonfly/` and checks the one against the
  other.  DragonFly publishes no signatures and nothing stronger than MD5,
  fetched over HTTPS: that is all the checking there is.  `build.sh` checks
  the uncompressed image against `md5.txt` again.
- `build.sh`: runs in the VM.  `info` reads the kernel version from the
  official image's kernel; `assemble` makes the image.
- `checks.sh`, `config-checks.sh`: `boot-test.py --platform dragonfly`'s
  checks.  The image has no Python, so they read the marker with `sed`.
- `build-rustic.sh`: cross-builds `rustic-bko` on the host (below).

## The image

| MBR slice | Contents |
|-----------|----------|
| 1 | Type 0xEF: the official image's FAT32 ESP, as is (`EFI/BOOT/BOOTX64.EFI` is `boot1.efi`) |
| 2 | Type 0x6C, active.  disklabel64 with `boot1_64`/`boot2_64`; `a` is the root, UFS, read-only |
| 3 | Type 0x0E: the config partition, FAT16 `BACKOTTER`, 32 MiB |

Laid out as DragonFly's own `nrelease` `mkimg` target does (MBR boot code,
256-head/63-sector geometry), with slice 2 cut to the system plus 256 MB
instead of filling a 1.9 GB image, and slice 3 added after it.  The root is
mounted from `part-by-label/BKO_<image id>.a`, with the image ID's dashes
left out (32 hex digits), a disklabel64 label of the image's own, so DragonFly's install media (all labelled `DragonFly_v6.4.2`)
in the same machine can't be taken for it.

What changes from the official image:

- Read-only root (`ro` in fstab, `root_rw_mount="NO"`); `/etc`, `/var`,
  `/root` and `/tmp` are tmpfs, the first three filled from the image at boot
  (`mount_tmpfs -C`), as DragonFly's own ISO does.
- Gone: the passwordless `installer` account (uid 0) and the prompt that
  points at it, `/firstboot`, and `rc.d/pfi`.  At every boot pfi looks for a
  `pfi.conf` on CDs and then on slice 1 of `da0`, `da1` and `da8` (read-only),
  and from the first it finds runs scripts and installs root's
  `authorized_keys`.  `da0s1` is often the machine's own ESP: not something a
  rescue stick should act on.  Root's
  password is locked; `remote-access` sets a one-time one for `su`.
- getty runs BackOtter's console (`stick/bsd/console`) as root on `ttyv0`
  and the serial port `ttyd0` (gettytab `bko.Pc` and `bko.9600`: `al=root`
  and `lo=` the console).  `ttyv1`-`ttyv7` log root straight into a shell
  (DragonFly's own `al.Pc`), as tty2-tty6 do on the Linux image.
- The loader and kernel console stay on the screen, DragonFly's default.
  Its kernel has one console: `boot_serial` would move it to COM1 and leave a
  PC's screen blank until BackOtter's console (with a panic or mountroot
  prompt unseen).  So nothing before getty reaches COM1 (`boot2` uses it
  only if a `/boot.config` asks, and the image has none), but the serial
  port gets BackOtter's console all the same.
- Host name `backotter`, DHCP (dhcpcd) on every interface, sshd off with
  `stick/bsd/sshd_config.backotter`'s lines first and no host keys
  (`remote-access` makes them).
- `restic-bko` and `rustic-bko` in `/usr/local/bin`, from
  `~/backotter-work/bko-bin/dragonfly/` (both required), the marker in
  `/.disk/backotter.json` (mode 444).  `zfs` is null: DragonFly has no
  OpenZFS.

## rustic-bko: cross-built

DragonFly's packaged Rust is 1.85.1 (6.4 and the development repository
alike), and rustic needs 1.91 (it and rustic_core use `let` chains, among
other things), so it can't be built natively in a guest as on NetBSD or
OpenBSD.  `build-rustic.sh [PORT]` cross-compiles it on the KVM host instead:
`x86_64-unknown-dragonfly` is a tier-3 target, with no std from rustup, so it
builds with a pinned nightly and `-Zbuild-std`, and the C code in the
dependencies and the link go through clang and lld against a sysroot copied
from a throwaway `--snapshot` copy of `dragonfly6`.  That guest also runs the
new binary's `--version` and `ldd` before it is kept.  It needs rustup for
the user (the nightly is installed if missing) and uses the host's clang and
lld, or unpacks Debian's clang-N/lld-N packages into its cache without
installing them.  Everything is cached in `~/backotter-work/dragonfly-cross`:
about 6 minutes the first time, under a minute after.  It writes
`bko-bin/dragonfly/rustic-bko` and `rustic-commit`, which says what was
built (rustic's and rustic_core's commits, and rustc).

Which sources:

- By default, branch `backotter` of github.com/mchobbit/rustic, built
  `--locked` (its Cargo.lock pins rustic_core's commit), as
  `netbsd/build-rustic.sh` does.  This needs the forks' DragonFly fixes
  (rustix 1.1.6 in rustic, the `dev_t` fix in rustic_core) to be pushed:
  until they are, the pushed branch fails to build (rustix 1.1.4 has no
  `ip_mreq_source` on DragonFly).
- `SRC=DIR build-rustic.sh` builds local trees, `DIR/rustic` and
  `DIR/rustic_core` (with or without `.git`; `RUSTIC_COMMIT` and
  `RUSTIC_CORE_COMMIT` name the commits otherwise): `../prep-rustic-src.sh`,
  which the native builds use too, points rustic's `[patch.crates-io]` at
  rustic_core and its vendored walkdir and refreshes Cargo.lock for them, and
  `rustic-commit` says "local, unpushed".

The binary links libc, libm, libpthread and libexecinfo dynamically, all in
the image's base.  It passed `testvm/compare/dragonfly/roundtrip.sh` with
restic-bko in all four directions on HAMMER2 and UFS.

## Testing in QEMU

`boot-test.py` boots DragonFly differently from the other platforms
(`MACHINE`, `USB_CONTROLLER` and `TYPING_RATE` there):

- the `pc` machine with an EHCI controller: DragonFly 6.4's xhci enumerates
  no USB device QEMU emulates, and on q35 its interrupts go astray (AHCI
  timeouts, then a hang just before it mounts root);
- typing at 960 bytes a second: QEMU's serial port has no speed of its own,
  and DragonFly's sio, at 9600 baud, drops input that arrives faster.  The
  long checks are written to `/tmp/bko-checks.sh` first, so the typed lines
  stay short.
