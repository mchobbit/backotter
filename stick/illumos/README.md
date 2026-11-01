# The illumos rescue image

`../make-img-bsd.sh illumos` makes `backotter-live-illumos-VERSION-amd64.img`,
an OmniOS r151058 system that runs from RAM, built in a throwaway
`--snapshot` copy of the `omnios58` test guest (`testvm/make-omnios.sh`) with
OmniOS's own tools for its install media (kayak).  Write it raw to a USB
stick.  It boots with BIOS or UEFI, but not with Secure Boot on: illumos'
loader isn't signed.  `platform` is `illumos`, the family of systems it
restores; OmniOS is its base, as Ubuntu is the Linux image's.

```
./make-img-bsd.sh [--test] illumos [OUT_IMG]
./boot-test.py IMG bios --platform illumos
./boot-test.py IMG uefi --platform illumos
```

## Files

- `platform.conf`: the release, the build VM, and `fetch`, which clones
  kayak (github.com/omniosorg/kayak), checks that the pinned commit is on its
  `r151058` branch, and keeps `git archive` of that commit as
  `~/backotter-work/img-media/illumos/kayak-fea4340.tar`.  No OS image is
  downloaded: the system comes from OmniOS's package repository
  (pkg.omnios.org/r151058/core), every package signed.
- `build.sh`: runs in the VM.  `info` gives the VM's kernel and ZFS features;
  `assemble` makes the image (below).
- `pkglist.backotter`, `keep.backotter`, `services.backotter`: what the image
  has beyond kayak's installer: packages, files kept from them, SMF services.
- `console.xml`, `on-tty`: the console service; `network.xml`, `net-up`: DHCP.
- `mkgpt.py`: writes the GPT (run by `build.sh` with the VM's Python).
- `checks.sh`, `config-checks.sh`: `boot-test.py --platform illumos`'s
  checks.  The image has no Python, so they read the marker with `sed`.
- `build-rustic.sh`: builds `rustic-bko` natively in a copy of `omnios58`.

## How it's built

kayak's `build/miniroot` makes OmniOS's installer system: it installs a list
of packages into a fresh image (`pkg image-create`), seeds the SMF repository
from a list of services, then removes every file no keep list names (about 90
% of them), strips the binaries, and checks that what's left has the
libraries it links.  `build.sh` runs it from the pinned commit, changed in
five checked places:

- the publisher requires signatures (`signature-policy=require-signatures`,
  with OmniOS's CA approved from the VM's `/etc/ssl/pkg`);
- the packages are pinned to the VM's own version (its
  `osnet-incorporation` and `release/name`), and kayak's `pkg update` after
  the install is skipped, so that the VM's `svccfg` matches the image's
  repository (kayak warns that a mismatched one can hang) and the marker's
  kernel is the VM's.  Update the VM (`testvm/vm-update`) to build from
  newer packages;
- BackOtter's services and their scripts go in before the repository is
  seeded (kayak imports manifests once, at build time; the image never runs
  `manifest-import`);
- its sanity step stops the build only for a missing library that isn't
  loaded lazily: its baseline is of the packages kayak's commit was released
  with, and every weekly update changes it a little;
- it stops after that step: `build.sh` makes the boot archive itself, without
  kayak's installer (`/kayak`, `/.initialboot`).

Then BackOtter's files go in (the binaries, the marker, `sshd_config`, the
host name), the boot archive is made (a UFS image of the whole system,
`devfsadm -r` on it, gzipped, as kayak's `build/iso` does), and the image is
laid out as OmniOS's own USB image (`build/usb`), with the config partition
added after it.

A build takes 6 minutes (Cinqcent) to 13 (Chopin) with the VM's 4 CPUs,
most of it kayak's: package downloads, the cull and stripping.

## The image

| GPT # | illumos slice | Contents |
|-------|---------------|----------|
| 1 | s0 | EFI system partition, FAT32, 34 MiB: `loader64.efi` as `EFI/BOOT/BOOTX64.EFI` |
| 2 | s1 | Solaris boot (V_BOOT), 1 MiB: `gptzfsboot`, the BIOS loader's second stage |
| 3 | s2 | Solaris root (V_ROOT), UFS: `/boot`, the kernel, `boot_archive.gz`, the marker |
| 4 | s3 | Microsoft basic data: the config partition, FAT16 `BACKOTTER`, 32 MiB |

The loader (pmbr then `gptzfsboot` with BIOS, `BOOTX64.EFI` with UEFI) reads
`/boot` from partition 3 and loads the kernel and the boot archive into RAM;
the kernel then runs with the archive as its root, a RAM disk.  Nothing on the
stick is mounted, so nothing on it is written, and the system is writable
everywhere (but only until the next boot).  The image is about 190 MB; the
booted system uses about 700 MB of RAM (the RAM disk is about 340 MB of
it), so a machine needs 1 GB at least.

What changes from OmniOS's installer:

- No installer: no `/kayak`, no `/.initialboot`, and root's password is
  locked (`*LK*`); `remote-access` sets a one-time one for `su`.
- BackOtter's console (`stick/bsd/console`) runs as root on the screen
  (`/dev/console`) and on COM1 (`/dev/term/a`, 115200 baud), and root shells
  on the virtual consoles 2-6 (Alt-Ctrl-F2 ...), as tty2-tty6 on the Linux
  image.  The screen and the virtual consoles are `system/console-login`'s
  own instances (`default`, `vt2`-`vt6`, from SMF's seed repository) with
  `on-tty` as their start method instead of ttymon and login; COM1 is
  `svc:/application/backotter/console:serial`'s.  `on-tty` makes the
  terminal its controlling one with `setpgrp`, as kayak's `takeover-console`
  does.  An instance whose device the machine lacks turns itself off for the
  boot; if the kernel's console is COM1 (`os_console=ttya` at the loader),
  `console-login:default` is BackOtter's console there and the serial
  instance stays off.
- With the console on the screen, svc.startd asks for a maintenance login
  early in every boot ("Console login service(s) cannot run"; OmniOS's
  installer has it too, and its `takeover-console` takes the screen back), and
  holds console-login back until it ends.
  `svc:/application/backotter/console-release` ends it, as Ctrl-D at its
  prompt would.
- The loader's menu shows on the screen and COM1 (`console="text,ttya"`); the
  kernel's console is the screen, so its messages and a panic show there and
  not on COM1.
- DHCP on every link (`svc:/application/backotter/network`, `net-up`, which
  also takes DNS from the lease), host name `backotter`, sshd
  (`network/ssh`) off with `stick/bsd/sshd_config.backotter`'s lines first
  and no host keys (`remote-access` makes them).
- autofs is left out, so `/home` is a plain directory for `remote-access`'s
  user.
- `system/idmap` is on: restic-bko and rustic-bko map Windows SIDs (file
  owners, ACL entries) to and from IDs through libidmap, and without
  `idmapd` every SID restores as `nobody`.  With no domain configured, a SID
  gets an ephemeral ID (2147483648 and up) for the boot.  idmapd links the
  AD, LDAP and Mozilla NSS libraries even then, so the image keeps them.
- More tools than the installer keeps (`keep.backotter`): fsck for UFS and
  FAT, `iostat`, `cfgadm`, `hotplug`, `eeprom`, `raidctl`, `stmsboot`,
  `fcinfo`, `luxadm`, `fwflash`, `runat`, `idmap`, `useradd`, PAM, OpenSSH
  and OpenSSL, GNU coreutils and the xpg4 tools, terminfo, keymaps and time
  zones.  OmniOS has no `mpathadm` or `sasinfo` packages.
- `restic-bko` and `rustic-bko` in `/usr/local/bin`, from
  `~/backotter-work/bko-bin/illumos/` (both required), the marker in
  `/.disk/backotter.json` (mode 444).  `zfs.version` is `illumos-r151058`
  (illumos' ZFS isn't OpenZFS), and its features are the VM's
  `zpool upgrade -v`.
- No pool is imported at boot: there's no `zpool.cache` and no root pool,
  and no `/etc/hostid`, so each boot makes its own (a restore imports with
  `zpool import -f`).
- NILFS2 and F2FS: illumos has neither, so the image has no tools for them.

## Finding the stick

The root is a RAM disk, so "the disk the root is on" means nothing here.
`config-checks.sh` finds the config partition as docs/design/stick-config.md
says for illumos: the disk whose slice `s3` (partition 4) has the marker's
size, and whose first sector has the marker's FAT label and volume serial,
reading only that sector of each disk and mounting nothing else.

## rustic-bko

`build-rustic.sh [PORT]` builds it natively in a throwaway copy of
`omnios58`, with OmniOS's Rust, gcc 14 and cmake (`../build-rustic-native.sh`
does the work), into `bko-bin/illumos/` (`bko-bin/omnios` on the hosts is a
link to it, for older commands).  `restic-bko` is the restic fork's
cross-build for `GOOS=illumos`.

## Testing in QEMU

`boot-test.py` boots illumos on the `pc` machine (`MACHINE` there), as the
`omnios58` guest runs, with the default xHCI controller.
