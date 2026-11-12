# Chimera Linux support (approved)

2026-11-11. The user's idea ("I'm thinking of adding Chimera Linux
support"). Approved by the user the same day, with the recommended decisions:
three guests, root on ZFS for the Thinkpad, HoltTest1Obscure, and a supported
system. C1 (the `chimera` guest) was built the same day, and its *checks* are
settled below: see "What C1 found".

**Conventions.** est. marks an estimate. *Check* marks a fact the docs don't
state, to be settled on the first guest before the code that depends on it.
Chimera's facts come from chimera-linux.org/docs (installation, apk, services,
FAQ), the `live/latest` directory of repo.chimera-linux.org, and the
chimera-live and chimerautils READMEs, read 2026-11-11.

## Summary

- Chimera Linux becomes a supported system for bko-holt and both archivers
  (rustic-bko and restic), with three QEMU guests, a SticTest1 entry each, a
  HoltTest1 group, and one BackShot1 run on the Thinkpad.
- Most of it is already built for other systems: musl (Alpine, Void),
  dinit (Artix), apk (Alpine), initramfs-tools (Debian), GRUB, systemd-boot,
  LUKS, LVM and root on ZFS through initramfs-tools (`debian13-zfs`).
- What's new is the userland: Chimera's core tools are FreeBSD's
  (chimerautils), not GNU's or BusyBox's. bko-holt's GNU-only commands all
  run in the rescue system, so they aren't affected. BackShot1's scripts,
  which run on the source and the restored system, have a few.
- ~3 d (est.), mostly the guests.

## Why

- **Another userland.** Every Linux guest so far has GNU coreutils or BusyBox.
  Chimera is the one maintained distribution with a BSD userland on Linux, so it
  finds GNU assumptions in whatever bko runs on the machine itself: the
  BackShot1 scripts, SticTest's checks and the commands bko-holt runs in a
  chroot.
- **A combination no guest has:** musl, dinit, apk-tools v3, initramfs-tools and
  LLVM-built packages together. Each piece is tested somewhere already; the
  combination isn't.
- **Root on ZFS with prebuilt modules** (`linux-lts-zfs-bin`), on a rolling
  distribution whose OpenZFS can be newer than the rescue image's. That tests
  the one-kernel rescue's rule that a restore recreates the source's pool
  features.

## What Chimera is (as far as bko cares)

| | Chimera | Nearest supported system |
|---|---|---|
| Release | Rolling (alpha since 2023-06); snapshots dated, e.g. 20261103 | Arch, Void |
| os-release | `ID="chimera"` (quoted); no VERSION_ID line at all (C1) | |
| libc, toolchain | musl, LLVM/clang | Alpine, Void musl |
| Userland | chimerautils (FreeBSD's coreutils, findutils, grep, sed, sh...); util-linux for mount, swapon, lsblk, findmnt | none |
| Init | dinit; `dinitctl enable X` links `/etc/dinit.d/boot.d/X` | Artix dinit |
| Packages | apk-tools v3, interactive by default; repos `main` and `user` | Alpine (apk v2) |
| Kernels | `linux-lts`, `linux-stable` | |
| Initramfs | initramfs-tools: `update-initramfs -c -k all` | Debian |
| GRUB | `grub-x86_64-efi`, `grub-i386-pc`; `grub-install`, `update-grub` | Debian, Arch |
| systemd-boot | `systemd-boot` (without systemd): `bootctl install`, then `gen-systemd-boot`, with a kernel hook after that; ESP as /boot, or an XBOOTLDR /boot | EndeavourOS (via kernel-install) |
| LUKS, LVM | `cryptsetup-scripts`, `lvm2`; `/etc/crypttab` before update-initramfs | Debian |
| ZFS | `linux-lts-zfs-bin` (prebuilt) or `zfs-ckms` (built from source); initramfs-tools support built in; docs advise a separate ext4 /boot | `debian13-zfs` |
| Install | `chimera-bootstrap` (network, or `-l` from the live ISO), `chimera-chroot`, `genfstab -U` | Arch |
| Images | `chimera-linux-x86_64-ROOTFS-DATE-full.tar.gz` (and `-bootstrap`), `LIVE-DATE-base.iso`; `sha256sums.txt` with a minisign signature | Void's ROOTFS tarball |

Usr-merged throughout; a separate /usr isn't supported.

## What already works, and what doesn't

**bko-holt `dump`** (runs live on the source):

- It reads files and runs util-linux, LVM, cryptsetup, ZFS and efibootmgr
  commands, and no coreutils. Chimera has the same tools.
- The OS check (`holt/src/osrel.rs` `SUPPORTED`) refuses it: there's no
  `chimera` row.
- Installed packages come from `/lib/apk/db/installed`, which resolves on
  Chimera through the usr-merge symlink (`/lib -> usr/lib`). apk-tools 3.0.8
  still writes it in v2's text format (`C:`, `P:`, `V:` blocks; C1), so the
  existing reader does, and no v3 reader is needed.
- `PACKAGES` (`holt/src/boot.rs`) lacks Chimera's names: `grub-x86_64-efi`,
  `grub-i386-pc`, `linux-stable`, `linux-lts-zfs-bin`, `linux-stable-zfs-bin`,
  `zfs-ckms`, `cryptsetup-scripts`. Without them the report misses its boot
  stack.
- The initramfs generator is found by the package `initramfs-tools`, as on
  Debian. Kernel files are named `vmlinuz-V` and `initrd.img-V` by
  initramfs-tools, which `kernels_json` already reads. Chimera's `V` is the
  package version plus flavour, e.g. `6.18.62-0-generic` for `linux-lts`
  (C1): `vmlinuz-6.18.62-0-generic`, `initrd.img-6.18.62-0-generic`,
  `config-...`, and no `System.map`.

**bko-holt `load`:**

- The GNU-only commands in `load/commands.rs` (`stat -c` and `find -printf` for
  btrfs snapshots and atimes, `cp -a` for the LVM devices file) run in the
  rescue system against `/mnt/bko-target`, not in a chroot. Nothing to change.
- Initramfs: it falls through to `chroot T update-initramfs -u -k all`. That's
  right for Chimera as long as each kernel already has an initrd, which a
  restore gives it.
- GRUB: the non-Debian path, `grub-mkconfig -o /boot/grub/grub.cfg`, works;
  Chimera's `update-grub` is a wrapper for the same.
- systemd-boot: `bootctl install --no-variables` reinstalls the target's own
  systemd-boot, and the entries come back with the ESP. Chimera writes them
  with `gen-systemd-boot`, not kernel-install. So `load` runs `gen-systemd-boot`
  after the initramfs, when the target has it, so the entries match the
  restored kernels and their command line. It changes nothing when nothing
  differs; it matters after `--new-uuids`.
- ZFS: the pool, its datasets, `/etc/hostid` and `zpool.cache` are rebuilt as
  for `debian13-zfs`; initramfs-tools' zfs hook does the rest. The modules
  come back with `/lib/modules`, so neither ckms nor a package install is
  needed. With `zfs-ckms` and no prebuilt package, *check* (on `chimera-zfs`,
  C3) that ckms's own state (`/var/lib/ckms`) needs nothing after a restore.
  ckms runs from a kernel hook, `/usr/lib/kernel.d/10-ckms.sh` (C1).
- Init system: `load` doesn't touch services, so dinit needs nothing.

**BackShot1** (`testvm/realworld/backshot1`, runs on the source and the restored
system):

| Script | Line | GNU or Alpine assumption | Fix |
|---|---|---|---|
| bs1-adopt | 14 | `apk add bash`: fine (apk v3 asks only on a tty) | `--no-interactive` when it's v3 |
| bs1-adopt | 43-56 | `pkg_install` gives apk Alpine's names: `python3`, `avahi dbus` | an eighth column for Chimera, picked by os-release before the `apk` branch: `python`, `avahi`, `openssh`, `efibootmgr`, `bash` (all five exist, C1; there is no `python3` package, `python` gives the command) |
| bs1-adopt | 149 | `sed -i '1i TEXT'`: GNU's one-line `i` and `-i` with no suffix | `{ printf ...; cat "$conf"; } >"$conf.new" && mv`, the same on every system |
| bs1-backup | 52, 55 | `df -B1 --output=used/avail` | `df -Pk`, column 3 or 4, times 1024 |
| bs1-backup | 97 | `sha256sum`: FreeBSD's tool is `sha256`, but chimerautils has `sha256sum` too, GNU's output (C1) | none |
| bs1-adopt, bs1-common, ... | | `readlink -f`: FreeBSD's has `-f` | none |

The table comes from a grep of GNU-only options; the real test is running the
scripts on the guest (`bs1-qemu.py --virtio`) before the Thinkpad. dinit is
already in bs1-adopt (Artix), and the services are `sshd` and `avahi-daemon`
(C1: `avahi-daemon` comes from `avahi-dinit`, which `apk add avahi` installs
with it; dbus is enabled by default, as `dbus-daemon` in
`/usr/lib/dinit.d/boot.d`).

**SticTest1:** the archivers are static binaries and run on musl already
(Alpine, Void). Each guest's `check-inner.sh` runs on the guest, so it is
written for a POSIX sh and FreeBSD tools, and tried there. Alpine's has two
options BusyBox accepts and FreeBSD may not.

**The rescue image:** no change ([[one-kernel-rescue]]). If Chimera's OpenZFS is
newer than the rescue image's, `load` recreating the source's pool features
is what's tested. A pool feature the rescue image can't create fails the plan
with a clear refusal, not a broken pool.

## What C1 found

Settled on the `chimera` guest (snapshot 20261103, apk-tools 3.0.8,
chimerautils 15.1.2, dinit 0.22.0, musl 1.2.6, `linux-lts` 6.18.62),
2026-11-11:

- **os-release:** `ID="chimera"`, quoted, and no `VERSION_ID` line. `SUPPORTED`
  needs a row that matches any version, as AerynOS's does.
- **apk's database:** `/lib/apk/db/installed` in v2's text format. No new
  reader.
- **Kernel files:** `vmlinuz-V`, `initrd.img-V` and `config-V` in /boot, `V` as
  `6.18.62-0-generic`. initramfs-tools with `MODULES=most`, `COMPRESS=zstd`.
- **Services:** in `/usr/lib/dinit.d`; enabled by a link of the service's name
  in `/etc/dinit.d/boot.d`. `sshd` and `dhcpcd` exist; `avahi-daemon` is in
  `avahi-dinit`. Enabled by default (`/usr/lib/dinit.d/boot.d`): agetty
  (a getty on every active console, the serial one too), chrony,
  dbus-daemon, elogind, tmpfiles-clean, turnstiled, udevd.
- **`sha256sum`:** present (`/usr/bin/sha256sum` and `sha256`, both in
  chimerautils).
- **`sed -i`:** FreeBSD's. The next argument is always the backup suffix:
  `sed -i 's/a/b/' F` fails ("unterminated substitute pattern"),
  `sed -i -e 's/a/b/' F` leaves a backup `F-e`, and `sed -i '' ...` or
  `sed -i.bak ...` work. So bs1-adopt 149's fix stands, and any `sed -i` the
  BackShot1 scripts or check-inner.sh run on the guest is a bug there.
- **/bin/sh:** FreeBSD's sh (chimerautils). No bash in the `full` tarball; the
  `bash` package exists.
- **Other tools, for C4 and C6:** `df` has no `-B` or `--output` (`-Pk`
  works); `stat` has no `-c` (FreeBSD's `-f`); `find -printf` and `readlink
  -f` work; `date` has no `-d` (`-r` works); no `tac`, `gawk`, `setfattr` or
  `getfacl` (the `attr` and `acl` packages). `timeout`, `mktemp`, `base64`,
  `install`, `truncate`, `fallocate`, `chattr`, `lsattr`, `rsync`, `python3`
  are there.
- **Packages for C2 and C3:** `grub-x86_64-efi`, `grub-i386-pc`,
  `linux-lts`, `linux-stable`, `linux-lts-zfs-bin`, `linux-stable-zfs-bin`,
  `zfs`, `zfs-ckms` (2.4.4; it pulls in gmake, perl and python), `ckms`,
  `cryptsetup`, `cryptsetup-scripts`, `lvm2`, `systemd-boot`,
  `systemd-boot-efi`, `initramfs-tools`, `dosfstools` all exist.
  `systemd-boot` holds both `bootctl` and `gen-systemd-boot`, and runs the
  latter on every kernel change from `/usr/lib/kernel.d/99-gen-systemd-boot.sh`.
  `gen-systemd-boot` reads `/etc/default/systemd-boot` (absent by default) and
  names its entries by `/etc/kernel/entry-token`, `chimera` by default.
- **The `full` tarball:** 533 MiB unpacked, 389 packages (`base-full`), no
  kernel and no boot loader; apk's repository list is `main` only
  (`/usr/lib/apk/repositories.d/01-repo-main.list`). The installed guest is
  2.3 GiB on disk. `apk --no-interactive` is accepted.

## What C2 found

Tracing every branch `dump` and `load` take on the OS, the package manager
and the boot tools, 2026-11-11:

- **`SUPPORTED`:** a `chimera` row with version `*`, "Chimera Linux" in
  messages. `ID="chimera"` with no `VERSION_ID` passes `dump`'s and `load`'s
  checks.
- **`PACKAGES`:** besides the seven names above, `zfs` (Chimera's ZFS
  userland) and `ckms` were missing too. The report's `boot.loader.package`
  now names `grub-i386-pc` (BIOS) or `grub-x86_64-efi` (UEFI), which only
  Debian's names did before; `load` doesn't read it.
- **Nothing assumes Debian from initramfs-tools.** The only Debian-only
  branch is `update-grub`, taken by os-release ID (debian, ubuntu,
  linuxmint, zorin), so Chimera gets `grub-mkconfig -o /boot/grub/grub.cfg`.
  debconf is read only if `/var/cache/debconf/config.dat` exists; without
  it BIOS GRUB's install disk is the one whose MBR holds GRUB. No
  `/etc/kernel/postinst.d` or dpkg is touched.
- **One wrong note, left:** BIOS GRUB's plan notes that "debconf's
  grub-pc/install_devices still names the source's disks" and suggests
  `dpkg-reconfigure grub-pc`, on every system, Chimera's included. Arch's,
  Alpine's, Void's and others' golden plans have it too, so changing it is
  its own change, not C2's.
- **The chroot's commands:** `grub-install`, `update-initramfs -u -k all`,
  `env LANG=C grub-mkconfig -o ...`, and the SELinux check (POSIX sh). None
  uses a GNU-only option.
- **The golden plan:** made in C2 (1ac2eb9) from passing HoltTest1 run
  `chim2`'s dump of `chimera`, as the corpus's others were.

## What C3 found (load)

Read on the `chimera` guest (`systemd-boot` 256.11-r5, `/usr/bin/gen-systemd-boot`),
2026-11-11:

- **When `load` runs it:** UEFI systemd-boot, not through shim, os-release
  `ID` `chimera`, and the dump's `boot.packages` lists `systemd-boot` (the
  package holding `bootctl` and `gen-systemd-boot`). By the dump, as
  `update-grub` (by ID) and `dracut-rebuild` (by `eos-dracut`) are: Pop!_OS's
  `systemd-boot` package has no `gen-systemd-boot`, so the package alone
  isn't enough, and a test in the chroot would add a line to every systemd-boot
  plan. The boot stage is then `update-initramfs -u -k all`, `bootctl install
  --no-variables`, `gen-systemd-boot`; every other golden plan is unchanged.
- **Exit status:** 0 made the entries; 1 made nothing (`SD_BOOT_DISABLE_KERNEL_HOOK`
  set, or `bootctl is-installed` isn't `yes`), which the kernel hook
  (`99-gen-systemd-boot.sh`) doesn't count as failing; 2 to 6 are ESP or
  /boot checks (not a mount point, not an ESP, not FAT32, a separate /boot
  that isn't XBOOTLDR); 69 is no `bootctl`. `load` takes 0 and 1, warns on 1
  (the restored entries stay), and fails on the rest with their status.
- **root= never comes from the rescue system.** `gen-systemd-boot` doesn't
  read `/proc/cmdline`. Its command line is `SD_BOOT_CMDLINE` (from
  `/usr/lib/systemd/boot/systemd-boot`, then `/etc/default/systemd-boot`),
  `/etc/default/systemd-boot-cmdline` (or `/usr/lib/systemd/boot/cmdline`),
  and `SD_BOOT_CMDLINE_DEFAULT` (`quiet splash` by default) for the normal
  entry; `ro single` for the recovery one. If that has no `root=`,
  `/usr/lib/base-kernel/kernel-root-detect` adds it from `findmnt -no source
  /` in the chroot (the restored root as `load` mounted it): `PARTUUID=` from
  `/dev/disk/by-partuuid`, else a `/dev/mapper` path, else `UUID=`, else the
  node; `ZFS=DATASET` for ZFS, `rootflags=subvol=` for btrfs. So the entries
  name the target's root by its new identity, which is what `--new-uuids`
  (not built yet) needs; a `root=` the user wrote into those files stays as
  written, and rewriting it is `--new-uuids`'s job, with fstab and crypttab.
- It deletes and rewrites `/boot/loader/entries/TOKEN-*.conf` (`TOKEN` from
  `/etc/kernel/entry-token`, `chimera` by default): one entry per kernel in
  `linux-version list` and a `-recovery` one, the initrd when
  `/boot/initrd.img-V` exists. Hand edits to those entries are lost, as on the
  source's next kernel update.

## What C6 found

Every script that runs on the source or the restored system (bs1-adopt,
bs1-backup, bs1-common, bs1-list, bs1-manifest, bs1-to-stick, bs1-verify;
bs1-restore runs on the stick, bs1-ostree only on ostree) read for GNU- and
bash-only forms, and the suspects run on the `chimera`, `chimera-uefi` and
`chimera-zfs` guests (and bs1-adopt's dinit change on `artix-dinit`),
2026-11-12. bs1-adopt starts in FreeBSD's sh and re-execs bash once it has
installed it; the controller runs every other shell script with `bash`, and
the Python ones with `python3`.

- **The table's fixes stand,** with these differences:
  - `--no-interactive` when `apk --version` says `apk-tools 3`, in an
    `apk_add` that bs1-adopt's sh part and its `pkg_install` share. On a tty,
    plain `apk add` stops at "Do you want to continue [Y/n]?" (tried).
  - The `sed -i '1i'` rewrite writes the new text back into `sshd_config`
    itself (`printf` and `cat` into `.bs1`, then `cat` back), not `mv`, so its
    mode, owner and SELinux label stay. Chimera never takes that branch: it
    has no `/etc/ssh/sshd_config`, only `sshd_config.d`, which bs1-adopt
    already uses (as for AerynOS); `sshd -T` shows the drop-in's
    `PermitRootLogin prohibit-password`.
  - The `df` lines are three, not two: `space_in_use` in bs1-common
    (`df -B1 --output=source,used`) and bs1-list's `df -h --output=avail` as
    well as bs1-backup's. Now `df -Pk` (column 3 or 4, KiB, multiplied in bash)
    and `df -Ph`. On the guests `space_in_use` matched `df -Pk`'s sum.
- **Missed by the table:**
  - bs1-adopt's Alpine-only `apk add coreutils findmnt lsblk blkid sfdisk
    mount umount util-linux-misc` ran wherever apk is: not on Chimera now,
    which has util-linux (`findmnt`, `lsblk`, `blkid`, `sfdisk`, `swapon`,
    `mount`, `umount --recursive`) and no BusyBox.
  - The dinit branch looked for the service in `/etc/dinit.d` only (Artix's
    place); Chimera's are in `/usr/lib/dinit.d`, so `sshd` and `avahi-daemon`
    were "unknown". Both now.
  - Chimera's dbus-daemon never reads the policy `apk add avahi` puts in
    `/usr/share/dbus-1/system.d` (no inotify, no trigger), so `avahi-daemon`
    can't own `org.freedesktop.Avahi` and exits 255 at every start until a
    reboot. bs1-adopt now sends dbus `org.freedesktop.DBus.ReloadConfig`
    (`dbus-send --system --print-reply`) before it starts avahi-daemon,
    wherever a dbus is running.
  - `dinitctl start` returns once a process service is launched, and with
    `smooth-recovery = true` (Chimera's avahi-daemon) `is-started` says
    `STARTED` for some seconds while dinit restarts a dead process. So the
    dinit branch counts a service as up only when `dinitctl status` names a
    `Process ID` that is in /proc, the same one 3 s later (not one dinit
    restarted in between), and tries five times
    before it fails (and with it bs1-adopt). Checked on `artix-dinit` (dinit
    0.22.1: up, stopped, unknown and restarted services all told right).
  - `efibootmgr` isn't in `base-full`, and a systemd-boot install doesn't need
    it, but bs1-to-stick does. bs1-adopt adds it on a UEFI Chimera that lacks
    it (tried on `chimera-uefi` with it removed).
- **Not needed on the guests:** `python3` was there already
  (`/usr/bin/python3`, from `python`), so the eighth column's `python` is for
  an install without it; bs1-adopt installed only `bash` and `avahi` (and
  `efibootmgr` where removed). `openssh` and util-linux were there too.
- **Checked and fine on Chimera:** `mktemp` and `mktemp -d` without a
  template, `head -c`, `od -An -tx1`, `readlink -f`, `sed -E` and `sed -n
  's/\(..\)/\1/p'`, `tr -c`, `comm -13`, `cmp -`, `seq`, `du -sh`, `find
  -maxdepth -iname`, `grep -Eqx`/`-qxF`, `install -d -m`, `date -u +FMT`,
  `sha256sum`, `printf %b`, awk sums past 2^32, `apk info -v`, `mount --bind`
  and `-o remount,bind,ro`, `lsblk -dnro`/`-snlo`/`-lnpo`/`-no PKNAME`,
  `findmnt -rno`, `swapon --show`, `cat /proc/1/comm` (`dinit`), dinit's
  `boot` reading `/etc/dinit.d/boot.d` (`dinitctl enable` links there). No
  script uses `stat -c`, `date -d`, `grep -P`, `xargs -r`, `sort -V`, `tac`,
  `echo -e` or GNU `cp`/`mv` options on the source. bs1-common's
  `internal_mounts`, `space_in_use`, `check_coverage`, `make_tree`/`drop_tree`,
  `package_list`, `swap_names`, bs1-list (and `--resolve`) and a bs1-manifest
  record and compare all ran on the three guests: XFS, ext4 on LVM on LUKS2
  with the ESP as /boot, and ZFS root (`rpool/ROOT/chimera`).
- **Left as it is:** `wc -l` and `du -sh` pad with spaces (only bs1-list's
  columns show it). Chimera's `ssh-keygen` service runs `ssh-keygen -A` at
  every boot, so the RSA, ECDSA and `mldsa44-ed25519` host keys bs1-adopt
  removes (it doesn't name the last) come back; the controller's pinned
  ed25519 key under `HostKeyAlias` still decides, as on other systems that
  regenerate keys.
- **The rehearsals** (`bs1-qemu.py --virtio`, the C5 bko-holt 24b1cfef0663
  and rel4's rustic-bko) found three more:
  - The two above: every first run stopped at "avahi-daemon is running".
  - bs1-qemu.py gave a LUKS guest no `keys/disk-passphrase`, so bs1-backup
    stopped at the layout. It now copies the guest's `luks-keyfile` there,
    as ht1.sh passes it to load.
  - bs1-verify on `chimera-uefi` saw two changes it didn't expect:
    `boot/NvVars` (OVMF's variables on the ESP, outside `boot/efi` when the
    ESP is /boot) and `etc/kernel/entry-token` (the restore's `bootctl
    install` writes it again, same content, new mtime). bs1-manifest now
    expects the first and counts the second as remade. It expects
    `boot/EFI/*` too (from the review): with the ESP as /boot, as on the
    Thinkpad, the restore's `bootctl install` puts the package's systemd-boot
    there, which differs from the ESP's when the package was upgraded without
    a `bootctl update` (Chimera's package has no trigger for that).

  Then all passed, 2026-11-12: `chimera-uefi` runs `c6-f7` (Cinqcent, 174 s)
  and `c6-f8` (Chopin, 186 s), `chimera-zfs` `c6-f3` (Cinqcent, 183 s) and `c6-f4`
  (Chopin, 189 s); RAM-disk peaks 5.5 and 5.0 GiB.
- **Not run:** bs1-to-stick (needs a stick), and no rehearsal of `chimera`
  (BIOS; bs1-qemu's `--bios`).

## Guests

Built by `make-bootstrap.sh`, as for Void: from the Arch ISO's live system,
unpack `chimera-linux-x86_64-ROOTFS-DATE-full.tar.gz` into the new root,
`chimera-chroot` (or the script's own bind mounts) into it, and add the kernel,
boot loader and `openssh` with Chimera's apk. The snapshot date is fixed in the
script, like the other versions. `sha256sums.txt` is checked against Chimera's
minisign key, pinned in the script. Chimera publishes one key per release
batch, in cports (`main/chimera-image-keys/files/DATE.pub`), and minisign is on
both hosts, so the check runs there, as Void's does (C1). That makes
three guests:

| Guest | Firmware, loader | Layout | What it covers |
|---|---|---|---|
| `chimera` | BIOS + MBR, GRUB (`grub-i386-pc`) | /boot ext4, swap, / XFS | the plain case; HoltTest1's oneshot candidate |
| `chimera-uefi` | UEFI, systemd-boot | ESP (1 GiB) as /boot, / ext4 on LVM on LUKS2, keyfile in the initramfs as on `alpine324-luks` | systemd-boot without systemd; `gen-systemd-boot`; cryptsetup-scripts |
| `chimera-zfs` | UEFI, GRUB (`grub-x86_64-efi`) | ESP, /boot ext4, root on ZFS (`rpool/ROOT/chimera`, the docs' options), `linux-lts-zfs-bin` | ZFS through initramfs-tools; pool features against the rescue's |

Each one gets: `rebuild-one.sh` lines, `stictest1/GUEST/check.sh` and
`check-inner.sh`, `holttest1d/ram-sizes.tsv` rows, `classes.json` entries,
the hosts split (one on Cinqcent, two on Chopin, or as the queue's sizes
suggest), and a GuestTest1 grade. (C5: all three run on both hosts, each
under 6 GiB of RAM disk, so the queue needs no split; the `ram-sizes.tsv`
rows come from the runs' RAM-disk peaks, not left + 1.0, because a pass
deletes its disks.)

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| C1 | `make-bootstrap.sh chimera`: the plain guest; settle every *check* above on it (os-release, apk's installed database, kernel names, service names, `sha256sum`, `sed -i`, /bin/sh). **Done 2026-11-11:** built on Chopin, copied to Cinqcent (same SHA-256), `vm-update --try` ok | 0.75 d |
| C2 | bko-holt: the `SUPPORTED` row, the package names, an apk v3 reader if C1 needs one; a golden plan from `chimera`'s dump, unit tests | 0.5 d |
| C3 | `chimera-uefi` and `chimera-zfs`; `load` runs `gen-systemd-boot`; golden plans for both | 0.75 d |
| C4 | SticTest1 entries and runs for the three guests (rustic-bko and restic) | 0.25 d |
| C5 | HoltTest1 group and runs, on both hosts. **Done 2026-11-12:** the three guests joined 1Obscure; each passed first time on both hosts with bko-holt 24b1cfef0663 and rel4's rustic-bko: `chimera` runs `c5-1` (Cinqcent) and `c5-2` (Chopin), `chimera-uefi` `c5-3` (Cinqcent) and `c5-4` (Chopin), `chimera-zfs` `c5-5` (Chopin) and `c5-6` (Cinqcent); RAM-disk peaks 4.3, 5.1 and 5.8 GiB, so `ram-sizes.tsv` rows of D 6, 6 and 7 (the peak rule), MEM 4 | 0.25 d |
| C6 | BackShot1: the fixes in the table; `bs1-qemu.py --virtio` rehearsals on `chimera-uefi`, then a run on the Thinkpad. **Done 2026-11-12:** fixes c67ae63; rehearsals pass on `chimera-uefi` and `chimera-zfs` on both hosts; the Thinkpad, installed from Chimera's live ISO by Claude at the user's request (GPT: 1 GiB ESP as /boot, 4 GiB swap, ZFS `rpool` unencrypted with `rpool/ROOT/chimera` at / and `rpool/home`; `linux-lts` 6.18.62 + `linux-lts-zfs-bin`, systemd-boot with `root=` pinned), run `chimera-20261112-092235`: phase1, plan, phase2 and verify PASS first time (37,285 files compared, 0 unexpected), bko-holt 24b1cfef0663 and rustic-bko 0.11.4+backotter.9 | 0.5 d |
| C7 | Docs: README supported lists, HoltTest1's table, `osrel.rs`'s comment, memory. **Done 2026-11-12:** holt/README.md's list and test note, `osrel.rs`'s comment, BackShot1's README (C6), HoltTest1's 1Obscure row (C5) | 0.1 d |
| | *≈ 3 d in all* | |

Each piece is committed on main when its tests pass. Nothing is pushed until the
user says so.

## Risks

- **apk v3 changed the installed database's format.** Then C2 grows by a parser
  (~0.25 d); the format is documented in apk-tools' source.
- **Chimera's userland differs from FreeBSD's in places** (chimerautils adds
  some GNU options). The table may list fixes that weren't needed, or miss
  some. Running the scripts on the guest settles it; the portable spellings
  are harmless either way.
- **The rolling snapshot moves.** A fixed date in `make-bootstrap.sh` keeps the
  guest repeatable, but apk fetches from the live repository, so a rebuild
  gets newer packages ([[images-take-os-updates]] applies the same reasoning).
- **Root on ZFS with prebuilt modules is a combination Chimera's docs cover
  briefly.** If `linux-lts-zfs-bin` doesn't boot from initramfs-tools in QEMU,
  `chimera-zfs` waits, and the other two go ahead.
- **systemd-boot in BIOS-less QEMU.** It needs OVMF with variables, which
  `bs1-qemu.py` and the UEFI guests already use.

## Out of scope

- Chimera on other architectures (aarch64, riscv64 and others): bko is x86_64.
- The GNOME and Plasma desktops: the guests are console-only, as the other
  bootstrap guests are.
- BackShot2 or BackShot2Alpine cases on Chimera: later, through BackShot2All.
- Changes to the rescue image.

## Decisions (approved with the recommendations)

1. **Three guests** (`chimera`, `chimera-uefi`, `chimera-zfs`; recommended), or
   just `chimera` for now.
2. **The Thinkpad's install for BackShot1:** UEFI, systemd-boot with the ESP as
   /boot, root on ZFS (recommended: no BackShot1 run has used ZFS on real
   hardware, and it's Chimera's least-trodden path); or ext4, for a first run
   with fewer variables.
3. **HoltTest1's group:** the guests join **1Obscure**, the long tail
   (recommended); or a new name, such as **1Chimera**.
4. **A supported system** in bko-holt's `SUPPORTED`, named in its messages
   (recommended); or test guests only, with `dump` still refusing it outside
   the tests.
