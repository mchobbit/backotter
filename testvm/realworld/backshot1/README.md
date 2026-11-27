# BackShot1

A bare-metal backup and restore of a real laptop, driven over SSH by a Claude
session.  The person's only jobs are booting the rescue stick and clicking Done.

The machine is named in `thinkpad.conf`: "the Thinkpad", on its one NVMe disk,
backing up to a USB drive labelled `mybig512`.  Everything acts on the disk with
that serial number, never on a `/dev` name.  Since 2026-11-17 "the Thinkpad"
is a Lenovo A100 (F0J6006UFR) with a Samsung PM9C1a 512 GB (serial
`S7XMNF0Y787118`), still named `thinkpad`, on its onboard Ethernet
(`1c:83:41:e8:f7:38`); the runs before that were on a ThinkPad X13 with a
Micron 256 GB (`214532A60C34`).

It handles any Linux `bko-holt` supports, on any stack of partitions, LUKS, LVM,
md, btrfs and ZFS on that disk, booted by UEFI or BIOS (an ostree system such as
Bazzite by UEFI, without LUKS or LVM: "ostree" below).  It backs up every real
filesystem the running system has mounted from the disk, and stops, before
changing anything, if the disk holds a filesystem that isn't mounted.  Not
covered: the BSDs, illumos and Haiku, which need their own rescue images, and
btrfs subvolumes or ZFS datasets that aren't mounted.

An encrypted install needs its passphrase, in plain text with no final
newline (`printf '%s'`), in `backshot1/keys/disk-passphrase` on the backup
drive.  `bs1-adopt` sets its initramfs to unlock with that file whenever the
drive is plugged in, so nobody types it, not even at the restored system's
first boot ("Unlocking from the drive" below).

Unlike `bko-realworld-1a`, which does everything from the stick, the backup is
of the running system.  ext4 has no snapshots, so files that change meanwhile are
caught at different moments; `bs1-manifest` tells those from real differences.

## Before a run

- `bko-holt` must have `dump --only-disk`.
- `make-keys`, once, on the controller, makes BackShot1's own keys in
  `~/.ssh/backshot1`: a login key, and one fixed host key that every install and
  the stick present, pinned under the name `bs1-thinkpad`.  So a reinstall never
  trips a host-key warning, and the controller never talks to an unverified
  machine.  The host key's private half sits unprotected on the drive and the
  stick: fine for this testbed, nowhere else.
- The stick needs a Linux image with `key-access`, and those keys:

      stick/add-ssh-key.sh --host-key ~/.ssh/backshot1/ssh_host_ed25519_key IMG ~/.ssh/backshot1/id_ed25519.pub

  With them on the stick, it starts SSH at boot and lets the key in as
  `backotter`, with sudo.  Nobody types a password anywhere.  The keys
  live in the stick's BACKOTTER partition (`backotter/authorized_keys`,
  `backotter/ssh_host_ed25519_key`), so a reflashed stick has none: run
  `add-ssh-key.sh` on it again (IMG is then the stick's `/dev` name on the
  Mac, unmounted).  Without them the stick boots to its menu with SSH off;
  `to-stick` checks first (below).
- After each OS install, once, on the Thinkpad, from the backup drive:

      sudo sh /PATH/TO/mybig512/backshot1/bs1-adopt

  (`sh`, because Alpine has no bash until this installs it, and as root without
  the `sudo` on systems that have none.)  It sets the hostname, installs the
  keys, and adds sshd, mDNS and the tools the scripts use if missing, under
  systemd, OpenRC, sysvinit, runit, dinit or s6 (on Gentoo by `emerge
  --getbinpkg --noreplace`, from the binhost; an install like the
  Thinkpad's has them all, so nothing is emerged).  On NixOS it writes
  `/etc/nixos/backshot1.nix`, imports it from `configuration.nix` and rebuilds
  (on LUKS the LUKS step adds `backshot1-luks.nix` and rebuilds once more);
  a system built from a flake needs those settings added by hand.  On Guix
  System it writes `/etc/backshot1/system.scm`, which loads
  `/etc/config.scm` and adds BackShot1's settings, and reconfigures from it
  (the first time ~17 min: Guix clones its own repository); reconfigure from
  that file afterwards, not `/etc/config.scm`, or they're gone
  (docs/design/guix.md, "What GX8 found").  BackShot1's bko-holt must be
  static there, as on NixOS.  On TrueNAS the hostname, the host key,
  sshd at boot and mDNS go into its middleware's database, and the
  preflight wants a reboot after it ("TrueNAS", below).  On an
  install with LUKS it then sets up unlocking from the drive (below), and
  stops if the drive's key doesn't open every volume.  Reboot once after
  it, with the drive plugged in: the preflight wants a boot the drive
  unlocked.
- **The testbed never sleeps.**  GNOME's idle auto-suspend (15 min after
  the last input, at GDM too) suspended the Thinkpad during a phase 1's
  `check --read-data`, and the controller's SSH timed out (Fedora 45,
  2026-11-20).  So adopt first writes `/etc/systemd/sleep.conf.d/90-backshot1.conf`
  (under elogind `/etc/elogind/sleep.conf.d/`), `AllowSuspend=no` and the
  rest: logind refuses every desktop's suspend from then on, between the
  steps too, and the restored system has the same file.  `backshot1 adopt
  --sleep-on` (`bs1-adopt --sleep-on`) removes it.  The preflight, phase 1,
  verify and check-boot also hold a logind block inhibitor (sleep, idle,
  the lid switch; `systemd-inhibit --list` shows BackShot1's) while they
  run, which needs no adopt; without logind (no elogind under runit,
  OpenRC, sysvinit or shepherd) neither is there, and they say so.
- The USB Ethernet adapter stays plugged in: the images are wired-only.

## The run

    backshot1 stage TOOLS_DIR     scripts and tools onto the backup drive
    backshot1 phase1              back up; check --read-data; prints RUN=...

The person clicks Done, which authorizes erasing the internal disk.  The stick and
the backup drive stay plugged in.

    backshot1 to-stick            reboots into the stick, once, by BootNext,
                                  waits for it (7 min) and runs find-rescue

Before it changes any boot variable, `bs1-to-stick` mounts the stick's
BACKOTTER partition read-only and stops, naming the `add-ssh-key.sh` line
above, unless `backotter/authorized_keys` has a key line (as `key-access`
reads it) and `backotter/ssh_host_ed25519_key` is an OpenSSH private key; and,
where the drive has `backshot1/keys/` from `stage`, unless the first holds the
controller's key and the second is the pinned host key (`ssh-keygen -y`;
without `ssh-keygen` that comparison is skipped, and it says so).  A
reflashed stick without its keys cost two 7-min waits before this
(2026-11-21).

If that doesn't work, the laptop comes back up as it was, and the person boots the
stick by hand, then runs `find-rescue`.  It needs a UEFI system.  Where
there's no `efibootmgr` (AerynOS packages none), `bs1-efi` writes the same
entry, byte for byte, and BootNext through efivarfs; `BS1_EFI=efivarfs`
takes that way where `efibootmgr` is there too.

    backshot1 find-rescue         by the laptop's MAC addresses; hostname is backotter
    backshot1 plan NAME           reads the whole backup, plans; erases nothing
    backshot1 phase2 NAME         wipes, restores, compares, redoes the boot loader
    backshot1 reboot              BootNext is the restored system
    backshot1 wait-host           until `thinkpad.local` answers (10 min); its
                                  hostname and PRETTY_NAME

Once `thinkpad.local` answers again:

    backshot1 verify NAME         the checks; exit 0 and RESULT say PASS

On an ostree system, then the rollback, as below:

    backshot1 check-boot NAME     the deployment this boot started, against the baseline

The tree is mounted at `/run/bs1-src` and backed up as `/mnt/bs1-src`
(`--as-path`), so every snapshot is `latest:/mnt/bs1-src`, older backups' too.

`find-rescue` tries every address the controller's ARP cache has for each of
the laptop's MACs (two USB Ethernet adapters and the onboard port), since
stale entries for the same MAC stay there, and macOS's `arp` writes
`00:e0:...` as `0:e0:...`.  Only the address that is an unlocked rescue stick
with the internal disk's serial wins, and only that one is saved for `plan`
and `phase2`.  It pings the network of the installed system's wired adapter
(the one with one of those MACs), as `to-stick` saw it: the whole subnet down
to a /22 (a TP-Link Deco's 192.168.68.0/22), only the address's own /24 on a
wider one.  `BS1_PREFIX=192.168.1` says which /24 instead.

## The hands-free cycle

Repeated restores of one backup need nobody at the laptop, once the firmware
is set up:

- The stick must be in one of the laptop's own USB ports, not on a hub.  On
  the X13, behind an external hub, a boot aimed at the stick by BootNext
  reached it 1 time in 5 and otherwise fell through to the internal disk; in
  a laptop port it reached it 5 times in 5.
- On the X13, the firmware's `BootMode` was `Diagnostics`; those 5 boots
  were all in that mode, and `Quick` wasn't tried with the stick in a laptop
  port.  It can be set over SSH, as root, with no visit to the firmware's
  setup (where the firmware has `thinklmi`):

      echo Diagnostics > /sys/class/firmware-attributes/thinklmi/attributes/BootMode/current_value

  On the A100, BootNext reached the stick on its first try (2026-11-17).

- The stick and the backup drive stay plugged in, and the installed system
  (the one to start from) answers SSH.

Then:

    backshot1 cycle NAME COUNT --erase-serial SERIAL [--reformat MOUNT=TYPE]...

does COUNT rounds of `to-stick`, `plan`, `phase2`, `reboot`, `wait-host` and
`verify`, and stops at the first failure.  `--erase-serial`, the internal
disk's serial number from `thinkpad.conf`, is the authorization that
clicking Done gives by hand: this erases that disk, COUNT times.  The boot
entry is `bs1-to-stick`'s "BKO rescue", made again each round (it deletes
the last one), and the restore leaves BootNext on the restored system.  But
the restore's boot stage makes the source's own entries again (`efibootmgr
--create`, at the front of `BootOrder`), first deleting any entry for the
same partition and loader, so the count stays the same from round to round.

A system that makes no entry of its own (AerynOS: `bootctl install
--no-variables`) boots the ESP's removable-media loader, and left to choose,
a firmware that prefers a plugged-in stick would boot the stick again.  So
when no entry names a restored ESP, the restore makes a one-shot "BKO
restored" entry for that ESP's `EFI/BOOT/BOOTX64.EFI` (`bs1-efi oneshot`:
out of `BootOrder`, as "BKO rescue" is) and points BootNext at it.  Each
restore deletes the last round's first, so it stays one entry.

As a safety net the cycle still counts the entries after every round, in
efivarfs (the `Boot####` variables, so AerynOS's too), and stops once there
are more than `BS1_ENTRY_GROWTH` (8) more than after round 1; then delete the
copies (`efibootmgr -b XXXX -B`, or without it `bs1-efi delete LABEL`)
before the next cycle.

Before anything changes, the cycle refuses a run that needs a person each
round: one whose baseline's `unlock.txt` says `attended`, or that has no
`unlock.txt` (made before unlocking from the drive: restore it by hand, or
take a new backup); an ostree run (its rollback visit is at the console);
and a backup of a system not booted by UEFI.

Still needs a person:

- a restored system that doesn't come up: `wait-host` gives up after 10 min,
  and the cycle with it.  The next boot goes to the internal disk again, so
  getting back to the stick takes F12, and a hang takes the power button (a
  battery defeats a smart plug);
- a reboot into the stick that falls through to the installed system
  (`to-stick` gives up after 7 min, and says why the last `find-rescue`
  failed): boot the stick by hand, then carry on from `find-rescue`;
- a stick that hangs, loses its network or reboots during the restore:
  `phase2` gives up once the stick hasn't answered for 10 min, or once the
  restore has run for `BS1_RESTORE_LIMIT` minutes (300);
- the firmware's boot entries, once the cycle stops for them (above);
- an attended run's passphrase, and an ostree run's rollback visit.

## ostree: Bazzite, Silverblue, Kinoite

An image-mode system (`/run/ostree-booted`; docs/design/bazzite.md) has a
composefs overlay for `/`, so what BackShot1 backs up is fstab's tree, from
`bko-holt dump`'s `storage.backup_sources`: the physical root (`/sysroot`, the
btrfs subvolume holding `ostree/repo` and the deployments) at `/`, `/boot`,
the ESP, `/var/home` at `/home`, and `/var`.  The `/etc` and stateroot binds
are left out: their files are in the physical root already.

- **Hold the updates off** for as long as the run takes, from the backup to
  the last check: `systemctl disable --now uupd.timer` on Bazzite, and turn it
  back on (`systemctl enable --now uupd.timer`) on the restored system once
  it has passed.  Phase 1 and `verify` stop the updaters (uupd,
  rpm-ostreed-automatic, bootc-fetch-apply-updates) for their own boot
  anyway, and `verify` fails if one ran since the boot.
- **bs1-adopt installs nothing** there (`/usr` is the image's, read-only).
  If a tool BackShot1 needs is missing, it stops and names it: layer it with
  `rpm-ostree install`, reboot, and run bs1-adopt again.  Kinoite 44 has
  them all.  What it writes lands in the booted deployment's `/etc` only, so
  the rollback keeps its old hostname and host key.
- **Preflight refuses** (and so does phase 1) when the OS isn't one
  `bko-holt dump` supports (a Bazzite that moved to 45 stops here); a
  deployment is staged, or pending (the next boot would start another one
  than the booted: reboot first, or `rpm-ostree cleanup -p`); an rpm-ostree
  transaction or an updater is running; or a btrfs subvolume that `btrfs
  subvolume list` shows is neither mounted nor inside one that is.
- **Phase 1 fails** if `ostree admin status` or `/boot/loader`'s target
  moved while it ran: the backup would hold /boot and the root on either
  side of a deploy.  Its baseline gains `deployments.txt` (the deployments
  and their hostnames, which one booted, the BLS entries' `options`, the
  command line but `BOOT_IMAGE=` (GRUB's count of the disks), SELinux, rpm-ostree's list, the image, bootupd, grubenv,
  `lsattr -d` of each deployment root, `ostree admin status`), `links.txt`
  (per filesystem, the regular files with more than one link, and how many
  paths have each link count) and `space.txt` (each btrfs filesystem's bytes
  in use).  The manifest compares each regular file's link count and inode
  flags under `ostree/`.
- **What verify adds,** after holding the updaters off and checking that
  rpm-ostree shows no staged or new deployment, then the manifest:
  - the booted deployment is the baseline's, checksum *and* serial (the
    two deployments may be one commit, told apart only by serial, kargs and
    `/etc`);
  - `/` is a composefs overlay, `/sysroot` is on the restored disk;
  - SELinux, `ostree admin status`, the deployments and their hostnames,
    each BLS entry's `options`, `/boot/loader` and the command line are the
    baseline's;
  - the link counts and the deployment roots' flags are the baseline's;
  - each btrfs filesystem uses at most 5% more than before (restored onto
    `compress=zstd:1`, it is usually smaller; broken hard links would make
    the repository about three times as big);
  - `ostree fsck` passes (every object's checksum, which covers its
    content, mode, owner and SELinux label), `rpm-ostree status` works,
    `bootupctl status` says "At latest version", `bootupd-state.json` is
    unchanged (so a first-boot ESP rewrite shows), and grubenv has no
    `boot_counter`.

  A gained `FS_NOCOMP_FL` on a repository object is not a difference: btrfs
  sets it as the restore writes data that won't compress.  Hardlinked
  symlinks come back as separate symlinks, which ostree doesn't mind.
- **The rollback boots too.**  After `verify` passes, boot the restored
  rollback once from the GRUB menu at the laptop's console (the second
  entry, its title ending `(ostree:1)`; if no menu shows, hold Esc as GRUB
  starts), then `backshot1 check-boot NAME`:
  the booted deployment must be the baseline's rollback, with that entry's
  kargs, its own hostname and SELinux as before, and the deployments
  unmoved.  If the rollback lacks bs1-adopt's host key (adopt ran in the
  default deployment only), the controller refuses it, and the console is
  the only way: check by eye that `rpm-ostree status` marks the rollback
  (`.0`) as booted and `cat /proc/cmdline` lacks the default's own kargs.
  Then reboot: GRUB starts the default again by itself, and `backshot1
  check-boot NAME` must say so.  Each check adds a line to the restore's
  `BOOTS`.  Nothing on the laptop is written for this but by GRUB itself.

## PikaOS: rEFInd and booster

PikaOS 4 boots by rEFInd (files on the ESP, its options in
`/boot/refind_linux.conf`) with a booster initramfs (docs/design/pikaos.md).
It goes onto the Thinkpad from the stick, without the GUI:

    sudo bash /mnt/bs1-backup/backshot1/pika/pika-bootstrap.sh SERIAL \
        /mnt/bs1-backup/iso/pikaos4/PikaOS-Nest-GNOME-4.0-amd64-v3-26.08.20-4.iso \
        /mnt/bs1-backup/backshot1/pika/install.sh /mnt/bs1-backup/backshot1/keys thinkpad

(`testvm/realworld/pika-bootstrap.sh` and `testvm/pika/install.sh`, copied
to the drive's `backshot1/pika/`).  It erases the disk with that serial
and runs PikaOS's own installer (the GTK installer's automatic XFS layout
and pikainstall) in a chroot of the ISO's live image: rEFInd gets its NVRAM
entry, and the system its host name, the fixed host key and the
controller's key, so `bs1-adopt` can run over SSH at once (it has nothing
to install).  The restore builds booster's image anew in the rescue's
chroot (host-only, for this machine) and installs no boot loader: it only
makes rEFInd's NVRAM entry again.  So `verify` wants every file in
`EFI/refind` back with its content (`bs1-manifest`'s `KEPT`, though the
rest of the ESP may change) and `refind_linux.conf` unchanged, and
`booster-reassert.timer` is held through phase 1 and `verify` as snapper's
timers are.  Passed 2026-11-14, run `pika-4-20261114-134728`.
`testvm/realworld/pika-bootstrap-qemu.sh TEST_IMG ISO WORK_DIR` rehearses the
bootstrap in QEMU: a blank NVMe, the ISO on a USB drive, then the disk alone
by rEFInd's NVRAM entry.  It passed 2026-11-14, as did `bs1-qemu.py --virtio`
on `pikaos4`.

**On LUKS** (family P, 2026-11-27; not yet run on the Thinkpad), the same
command with `--luks` first:

    sudo bash /mnt/bs1-backup/backshot1/pika/pika-bootstrap.sh --luks SERIAL \
        /mnt/bs1-backup/iso/pikaos4/PikaOS-Nest-GNOME-4.0-amd64-v3-26.08.20-4.iso \
        /mnt/bs1-backup/backshot1/pika/install.sh /mnt/bs1-backup/backshot1/keys thinkpad

It is the GTK installer's "Automatic" Btrfs layout with encryption on
(install.sh's `btrfs-luks`, the `pikaos4-luks` guest's, but with rEFInd's
NVRAM entry rather than BOOTX64 mode): the same ESP and ext4 `/boot`
(`PikaOS`), then LUKS2 `crypt_root` (cryptsetup's defaults, argon2id)
holding Btrfs `@` (/) and `@home`.  The passphrase is
`keys/disk-passphrase`'s bytes (no final newline, printable ASCII: it must
be typeable at booster's prompt); the script copies it to its chroot's
`/run` for install.sh's `PIKA_LUKS_KEYFILE`, so `cryptsetup luksFormat` and
`luksOpen` read it with `--key-file`, and removes the copy once the install
is done.  It is never printed, logged or put on a command line (pikainstall
gets no `-c`: it only keys a separate `/home` volume).  Everything it mounts
is under `/run/bko-pika`, so `/mnt/bs1-backup` stays readable throughout.
The first boot asks for the passphrase (booster's prompt, Plymouth's with
`splash`); `bs1-adopt` then sets up family P (below, "Unlocking from the
drive"), and the next boot, with `mybig512` plugged in, must unlock with
nothing typed before the backup (`backshot1 unlock` says `UNLOCK=drive`).
`pika-bootstrap-qemu.sh --luks TEST_IMG ISO WORK_DIR` rehearses all of that
(the install, a typed boot, `bs1-adopt`, a boot with a fake XFS `mybig512`
and nothing typed, a boot without it), typing the passphrase blind through
QEMU's monitor; PASS on Cinqcent 2026-11-27, as did `bs1-qemu.py --virtio
--luks-typed --luks-off` on `pikaos4-luks` (Chopin, a whole backup and
restore with family P).  In QEMU booster asked in text on the screen
("Enter passphrase for crypt_root:"), not through Plymouth's splash, even
on the `quiet splash` line; without the drive it asked about 35 s into the
boot, after the helper's "waiting up to 30 s for the backup drive
mybig512" and "no backup drive mybig512; asking instead".

## AnduinOS: the MOK-signed GRUB and its snapshots

AnduinOS 2 signs its own GRUB with a MOK made on the machine, behind
Ubuntu's shim, and keeps Btrfs snapshots with its Disk Snapshots Manager
(docs/design/anduinos.md).  It goes onto the Thinkpad from the stick,
without the GUI:

    sudo bash /mnt/bs1-backup/backshot1/anduinos/anduinos-bootstrap.sh SERIAL \
        /mnt/bs1-backup/iso/anduinos2/AnduinOS-2.0.5-amd64.iso /mnt/bs1-backup/backshot1/keys thinkpad

(`testvm/realworld/anduinos-bootstrap.sh` and `testvm/anduinos/plan.py`,
copied to the drive's `backshot1/anduinos/`).  It erases the disk with that
serial and runs AnduinOS's own installer backend (its "erase disk, Btrfs"
plan and `anduinos-installer-executor`) in a chroot of the ISO's live root,
with the ISO's files copied once beside it (`.tree`: the installer wants its
live media on a disk, not a loop device).  The installer makes the NVRAM
entry; the script adds the controller's key and the fixed host key and
enables sshd, so `bs1-adopt` has nothing to install.  The restore keeps
shim, MokManager and GRUB as the ESP brings them back (`KEPT`), runs
`grub-install --target=i386-pc` and `update-grub`, and makes the entry
again.  The manager's scheduler is held through phase 1 and `verify`.
Passed 2026-11-14, run `anduinos-2.0.5-20261114-153639`.

## KDE neon: Calamares's layout, from the stick

KDE neon 24.04 User Edition (docs/design/kde-neon.md) installs with
Calamares, a GUI.  `neon-bootstrap.sh` does what neon's Calamares does,
from the stick and without the GUI:

    sudo bash /mnt/bs1-backup/backshot1/neon/neon-bootstrap.sh SERIAL \
        /mnt/bs1-backup/backshot1/neon/neon-user-desk-20261025-1825.iso /mnt/bs1-backup/backshot1/keys thinkpad

(`testvm/realworld/neon-bootstrap.sh`, copied to the drive's
`backshot1/neon/` beside the ISO, whose sha256 the script pins).  It erases the
disk with that serial (never a USB disk) and lays it out as Calamares does:
GPT, a 300 MiB ESP at /boot/efi, ext4 / and a 512 MiB /swapfile.  It copies
the live image, removes the live session's packages, installs the kernel,
openssh-server and shim, and runs `grub-install --bootloader-id=neon
--uefi-secure-boot`, which makes the NVRAM entry.  It also copies grub.cfg to
`EFI/ubuntu`, adds the controller's key and the fixed host key, and keeps
os-prober off with a grub.d drop-in, so the stick and mybig512 stay out of
grub.cfg.  About 5 minutes.  The restore runs `update-initramfs`,
`grub-install --bootloader-id=neon --no-nvram` and `update-grub`, then makes
the entry for `\EFI\neon\shimx64.efi`.  `bs1-adopt` needs nothing new.
Passed 2026-11-14, run `neon-24.04-20261114-141609`, with Secure Boot off in
the firmware.
`testvm/realworld/neon-bootstrap-qemu.sh TEST_IMG ISO WORK_DIR` rehearses the
bootstrap in QEMU under Secure Boot: a blank NVMe, the ISO on a USB drive,
then the disk alone by the NVRAM entry, with grub.cfg checked for other
disks' systems.  Written 2026-11-15, not yet run.

## Slackware (15.0 or pinned -current): installpkg and ELILO, from the stick

Slackware's setup is a dialog-driven ramdisk with no answer file.
`slackware-bootstrap.sh` installs it from the stick the way the
`slackware15-uefi` guest is built (`make-bootstrap.sh slackware-uefi`,
`bootstrap/slackware.sh`; docs/design/slackware.md).  The Thinkpad runs
slackware64-current since 2026-11-22, from one pinned tree:

    sudo bash /mnt/bs1-backup/backshot1/slackware/slackware-bootstrap.sh SERIAL \
        /mnt/bs1-backup/backshot1/keys thinkpad /mnt/bs1-backup/slackware-cache current-20261121

Leave out the last argument (RELEASE) for 15.0.
(`testvm/realworld/slackware-bootstrap.sh`; the copy on the backup drive
is in `backshot1/slackware/`, with `slackware-pin.sh` and the runs' logs.)

**The pin.**  -current changes daily, so every install and restore test of
the -current Thinkpad uses the tree `slackware-pin.sh` fetched on
2026-11-22: `slackware-cache/current-20261121` on mybig512, the ChangeLog
entry of Sat Nov 21 23:11:21 UTC 2026, CHECKSUMS.md5 SHA-256
`9418aee6...eb9917`, 3049 files (999 packages of a, ap, d, l and n, the
top-level files, `slackware64/`'s FILE_LIST and MANIFEST.bz2), 2.9 GB, from
slackware.osuosl.org.  It is read-only (`chmod -R a-w`) and has a
`PINNED.txt`; `testvm/realworld/slackware-current.pin` documents it in the
repo.  slackware-pin.sh checks CHECKSUMS.md5 with gpgv against Slackware's
key (the tree's GPG-KEY, held to its fingerprint), every file against its
MD5, and every package against its .asc.  To check the pin again, offline:

    bash /mnt/bs1-backup/backshot1/slackware/slackware-pin.sh \
        /mnt/bs1-backup/slackware-cache --check current-20261121

It fetches nothing: the pin's own GPG-KEY (held to the fingerprint), its
CHECKSUMS.md5 against its signature, PINNED.txt and the `PINS` table, every
file, every package's .asc, and that it is still read-only (2026-11-22, on
the -current Thinkpad: PASS, 83 s).  A run without `--check` pins what the
mirror has now, so on any later day it makes a new pin; with a pin in the
cache already it stops and asks for `--new` (or `--check PIN`), and when
the mirror is still at a pin it checks that one instead.
slackware-bootstrap.sh never fetches into a pin: it holds CHECKSUMS.md5 to
the SHA-256 in its `PINS` table, then checks each package as for 15.0, and
stops if one is missing or fails.  A new pin means a new `current-DATE` and
a new row in `PINS` (slackware-bootstrap.sh and slackware-pin.sh) and in
bs1-adopt's `slk_pin_sha`.

For 15.0 it first fetches what the cache lacks of the 15.0 tree from
slackware.osuosl.org: CHECKSUMS.md5, checked with gpgv against Slackware's
key (pinned by fingerprint), then the 809 packages of series a, ap, d, l
and n (all but kernel-huge), each checked against its MD5 and its own .asc.
Only then does it erase the disk with that serial, which must be an NVMe
disk: fstab and root= name the root by device, as setup writes them, and a
SATA disk is sdb beside the stick but sda without it.  It lays the disk out
as GPT: a 512 MiB ESP at /boot/efi and ext4 / for the rest, with no swap.
/ is made with the tree's own mke2fs.conf (15.0's has no orphan_file, which
its e2fsck refuses; -current's e2fsprogs 1.47.4 has it).  Then it installs
every package with Slackware's own `installpkg --root`, running the
doinst.sh scripts under bash.  After that it writes what setup's
configuration steps write: fstab and root= by device name,
`thinkpad.localdomain`, Europe/Paris, and DHCP on eth0-eth2.  The machine's
wired adapters are pinned to those names in `70-persistent-net.rules`,
onboard first.  The initrd is mkinitrd's: for 15.0 from
`mkinitrd_command_generator.sh`'s command, for -current by `geninitrd`
(setup's `setup.01.mkinitrd`, GENERATOR=mkinitrd, which runs the same
generator) into `/boot/initrd-VERSION.img`.  ELILO's four files go in
`EFI/Slackware` (and `EFI/BOOT`).  The NVRAM entry "Slackware" goes first,
and is also set as BootNext.  The disk's old entries are removed.  sshd is
on, root logs in by key only (its password field is `*`), and the fixed
host key is installed.  Slackware's key goes into root's keyring for
slackpkg.  There is a user `m` with no password: set one with `passwd m`.
-current has avahi and nss-mdns (series l and n): avahi-daemon runs
(`rc.avahidaemon`) and nsswitch.conf asks `mdns4_minimal` first, as
shipped, so `thinkpad.local` resolves.  Slackware 15.0 has no avahi: find
its address by the MAC (`arp -an` on the Mac).

-current, kernel-generic 7.3.5 (modules bundled; it has had no kernel-huge
or kernel-modules since 2024): two things differ from setup.
`/etc/default/geninitrd` gets `AUTO_UPDATE_GRUB=false`: ELILO boots the
machine, and update-grub's os-prober mounted the stick's and the backup
drive's partitions on the first run.  And the chroot's gpg (2.4) left
keyboxd and gpg-agent running there, which kept the target busy, so they
are stopped (`gpgconf --kill all`).  The ESP keeps a copy of the kernel and
initrd (ELILO boots `EFI/Slackware`'s, not /boot's), so after a kernel
upgrade run `eliloconfig` (or copy `/boot/vmlinuz-generic` and
`/boot/initrd-generic.img` to `EFI/Slackware/vmlinuz` and `initrd.gz`).
geninitrd makes the initrd itself when the kernel package is installed.
slackpkg's mirror is the pin itself,
`file:///mnt/bs1-backup/slackware-cache/current-20261121/`, never live
-current, so `slackpkg update` and `upgrade-all` keep the machine on its
pin (with mybig512 mounted at /mnt/bs1-backup; without it slackpkg finds no
mirror and stops).  Only series a, ap, d, l and n are in the pin, so
`slackpkg install` of anything else fails.  (Until 2026-11-22 the bootstrap
wrote osuosl's slackware64-current there; the Thinkpad's
`/etc/slackpkg/mirrors` was changed by hand, and `slackpkg update` against
the pin works.)

15.0: the tree is 15.0 as released (kernel 5.15.19); `slackpkg update &&
slackpkg upgrade-all` brings in patches/.  That includes kernel-generic and
kernel-modules, but the ESP keeps the old `vmlinuz` and `initrd.gz` in
`EFI/Slackware`, and the old /lib/modules is gone: the machine would boot
the old kernel without its modules, so without its network driver.  Before
rebooting, make the new kernel's initrd and copy both to `EFI/Slackware`:
yes at slackpkg's kernel prompts, or by hand, mkinitrd with the command
`/usr/share/mkinitrd/mkinitrd_command_generator.sh -r -k NEW_VERSION`
prints, then `eliloconfig`.  (`EFI/BOOT`, the fallback, keeps the old
pair.)

**bs1-adopt on Slackware** (2026-11-22).  Its PID 1 is sysvinit's, but
the services are rc.d's: adopt makes `/etc/rc.d/rc.NAME` executable (so
rc.M, or rc.inet2 for sshd, starts it at boot) and runs `rc.NAME restart`,
or `start` when that fails (`rc.sshd restart` stops only the listener, so
the controller's session stays; its start makes the RSA and ECDSA host keys
again, which adopt removed: harmless, the controller pins the ed25519 one).
`/etc/HOSTNAME` keeps its fully qualified form, the domain as it was
(`thinkpad.localdomain`), because rc.M gives the kernel that name; bs1-common's
`this_host` reads the kernel's name up to its first dot.  avahi: the avahi
user and group, `rc.messagebus` and `rc.avahidaemon` executable, and
`mdns4_minimal` in nsswitch.conf (all as -current ships them).  The
sleep.conf drop-in goes in `/etc/elogind/sleep.conf.d`, and elogind honours
it (`CanSuspend` said `no` after adopt).  The package list is pkgtools'
(`/var/lib/pkgtools/packages`), not Slackware's rpm.  Packages are never
pulled with slackpkg: a missing one is installed with `installpkg`, by
name, from the tree on mybig512 the system was installed from
(`slackware-cache/current-DATE` or `slackware64-15.0`), the one tree whose
packages are exactly the installed ones.  Only trees checked as
slackware-bootstrap.sh checks them count: `slackware64-15.0`, or a pin
bs1-adopt knows (`slk_pin_sha`, the `PINS` SHA-256, also its PINNED.txt's;
never a `.part` slackware-pin.sh left), with CHECKSUMS.md5 signed by
Slackware's key (the pin's GPG-KEY, or `slackware-key.gpg` for 15.0, held
to the fingerprint); each package is held to its MD5 there and to its own
.asc before installpkg.  A system that took a package from anywhere else
matches no tree, and two trees that both match aren't guessed between:
adopt stops and says to install it by hand.  A full install
(series a, ap, d, l and n, as `slackware-bootstrap.sh` makes it) lacks
nothing.  bs1-verify waits for rc.M to finish (sshd is up before it is).
bs1-manifest's Slackware churn (a reboot of the -current Thinkpad,
2026-11-22): rc.M's cache updates at every boot (all of `usr/share/mime`,
gschemas.compiled, intent.cache, gdk-pixbuf's loaders.cache), rc.udev's
hwdb.bin come back with the same bytes; `etc/adjtime` (rc.6's hwclock) and
`etc/resolv.conf.bak` may differ, and so may `etc/ntp.conf` where dhcpcd's
ntp hook is installed: it writes the lease's NTP servers at every lease, and
a router may hand out another one (the Thinkpad's did, 2026-11-23).

**bko-holt takes the pinned -current** since 24d0f28 (dump and load:
geninitrd's initrd, the kernel at `/boot/vmlinuz-VERSION`, ELILO).  The
Thinkpad's first BackShot1 on it, slackware-16.0-20261123-042127: 379,312
files, the restored system up by itself, no unit failed.

The stick isn't setup's ramdisk, so installpkg runs with Slackware's own
tar, `--numeric-owner`, and bash as `sh`.  The doinst.sh scripts that
chown by name run a second time in the chroot.  Ubuntu's tar 1.35 drops
every `var/log/setup` file (it won't extract through a symlink; -current's
own tar 1.35 does, tried 2026-11-22), and names map to Ubuntu's IDs.  All
four are still needed for -current.  Run on the Thinkpad: 15.0 on
2026-11-22, about 9 minutes with the cache full, root SSH at
192.168.68.67 68 s after the reboot.  -current (pin current-20261121) the
same day, about 11 minutes: it booted kernel 7.3.5 by the NVRAM entry, and
`ssh root@thinkpad.local` answered 72 s after the reboot (by address too,
192.168.68.65); mybig512 mounts there (`mount -L mybig512 /mnt/bs1-backup`).

## Guix System from the stick, plain or on LUKS (family G)

Guix System 1.5.0 goes onto the Thinkpad from the stick the way Guix's
manual installs from another distro (docs/design/guix.md, "What GX8 found"):
`testvm/realworld/guix-bootstrap.sh`, which reads Guix's binary tarball and
the drive's `keys/`.  The drive's `backshot1/guix/` holds the script,
`guix-binary-1.5.0.x86_64-linux.tar.xz` (SHA-256 `aa410254...f47817`), its
`.sig` and GNU's `gnu-keyring.gpg`.  This checks it there, and must say "Good
signature" (Efraim Flashner's key `A28BF40C...8351`):

    cd /mnt/bs1-backup/backshot1/guix && gpgv --keyring ./gnu-keyring.gpg \
        guix-binary-1.5.0.x86_64-linux.tar.xz.sig guix-binary-1.5.0.x86_64-linux.tar.xz

Then `backshot1 to-stick`, and on the stick (`ssh -t` as `backotter`, at
the address `find-rescue` saved, with the drive mounted at `/mnt/bs1-backup`).
Without encryption (the layout in the script's header):

    sudo bash /mnt/bs1-backup/backshot1/guix/guix-bootstrap.sh SERIAL \
        /mnt/bs1-backup/backshot1/guix/guix-binary-1.5.0.x86_64-linux.tar.xz /mnt/bs1-backup/backshot1/keys thinkpad

With the root on LUKS1 (`--luks`: `guix15-luks`'s layout, the ESP and swap
plain), in three steps.  The passphrase is typed by the person, never read
from a file, so the middle step is theirs:

    sudo bash /mnt/bs1-backup/backshot1/guix/guix-bootstrap.sh --luks prepare SERIAL
    sudo cryptsetup luksFormat --type luks1 ... /dev/nvme0n1p3 && sudo cryptsetup open /dev/nvme0n1p3 cryptroot
    sudo bash /mnt/bs1-backup/backshot1/guix/guix-bootstrap.sh --luks install SERIAL \
        /mnt/bs1-backup/backshot1/guix/guix-binary-1.5.0.x86_64-linux.tar.xz /mnt/bs1-backup/backshot1/keys thinkpad

`prepare` wipes and partitions the disk and prints the exact `cryptsetup`
line.  Type the passphrase in `keys/disk-passphrase`, or adopt's LUKS step
stops: the drive's key must open the volume.  LUKS1, since Guix's GRUB 2.12
has no Argon2.  `install` refuses unless `cryptroot` is open on that disk's
third partition, LUKS1.  It writes a `config.scm` with the volume as a
`luks-device-mapping` mapped device by its LUKS UUID and the file systems on
`/dev/mapper/cryptroot`, as Guix's installer does.  It checks that GRUB's
image reads `/boot/grub` from the volume, and closes `cryptroot` at the end.
A failed `install` unmounts everything but leaves `cryptroot` open: run it
again.  `guix system init` takes ~13 min either way.  Each run prints
`BKO-GUIX-INSTALL-OK` last.  `grub-install` leaves "Guix" first in BootOrder.

On the LUKS install, each boot asks twice at the laptop's console until
adopt: first GRUB's `Enter passphrase for hd0,gpt3 (...)`, then the
initrd's.  Both read the US layout, so on the A100's French keyboard type
where the US keys would be.  Then `backshot1 wait-host` and `adopt`.
Adopt's Guix arm does its first reconfigure (~17 min); on LUKS its family G
then makes GRUB and the initrd unlock from the drive ("Unlocking from the
drive").  Reboot with the drive plugged in and nothing typed, then
`unlock` (it must say `drive`), `preflight`, `phase1` and `cycle NAME 1
--erase-serial SERIAL`.  `testvm/realworld/guix-bootstrap-qemu.sh [--luks]
TEST_IMG TARBALL WORK_DIR` rehearses either mode in QEMU.

## Void musl on encrypted ZFS, ZFSBootMenu (family Z)

Void Linux (musl) with its root on a natively encrypted ZFS pool, booted by
ZFSBootMenu's v3.1.0 release EFI image: the `void-zbm-enc` guest's layout
(ZFSBootMenu's own Void guide) on real hardware, with Xfce and LightDM.
Pool `zroot`, boot environments `zroot/ROOT/void` (Void's default kernel and
a newer series, linux7.2 on 2026-11-19) and its clone `zroot/ROOT/void-6.18`
(the default kernel only), `zroot/home` and `zroot/var/log` shared.
`testvm/realworld/void-zbm-bootstrap.sh` installs it from the stick; its
header has the layout and the two musl gotchas.

The inputs are pinned by SHA-256 in the script; their signatures are checked
on a farm host, as make-void-zfs.sh and make-bootstrap.sh check them.  The
rootfs check needs `minisign` (Void's key), the ZFSBootMenu check
`signify-openbsd`: every build host (Chopin, Cinqcent, Copland, Cage,
Czerny) has both, and Choubox has no minisign (2026-11-27).  Each check
runs where its inputs are: `void-musl-rootfs/` is in Cinqcent's
rebuild-inputs only and `void-zbm/` in Chopin's only, until rebuild-inputs
are copied to every build host (docs/design/img-speed.md F12).  On a host
that has both directories the two commands can go in one ssh.  Each line
must print OK:

    ssh m@cinqcent.local 'cd /bkoscratch/rebuild-inputs/void-musl-rootfs &&
      minisign -q -V -P RWT5+SoQD+dWTeOdNuc4Q/jq2+3+jpql7+JJp4WukkxTdpsZlk2EGuPj -m sha256sum.txt -x sha256sum.sig &&
      sha256sum -c --ignore-missing --quiet sha256sum.txt && echo VOID-SIG-OK'
    ssh m@chopin.local 'cd /bkoscratch/rebuild-inputs/void-zbm &&
      grep -qxF RWSUawlGznCLF8NxEb1EyrGpP2eIR9l7JIZgLzCdv+64uBDvfXNJm+cF zfsbootmenu.pub &&
      signify-openbsd -V -q -p zfsbootmenu.pub -x sha256.sig -m sha256.txt &&
      sha256sum -c --ignore-missing --quiet sha256.txt && echo ZBM-SIG-OK'

`void-x86_64-musl-ROOTFS-20250202.tar.xz` (from `void-musl-rootfs/`),
`zfsbootmenu-release-x86_64-v3.1.0-linux6.12.EFI` (from `void-zbm/`) and the
script go onto the drive's `backshot1/void-zbm/`, while the installed system
(or a Void live ISO) has the drive mounted; `sha256sum` them there.  The
script keeps xbps's package cache and every run's log
(`install-DATE-exitRC.log`) in that directory too.  The release image goes
on the ESP as released (no `zbm-kcl`): `bs1-adopt` rewrites its `.cmdline`.

Then `backshot1 to-stick` (it works from Void's live ISO as well: plain
`reboot`, the live system's efibootmgr), and on the stick, detached, since
it takes a while (two DKMS builds of zfs, ~1.5 GB of packages):

    sudo bash /mnt/bs1-backup/backshot1/void-zbm/void-zbm-bootstrap.sh --detach SERIAL thinkpad

(or from a copy in the stick's `/tmp`: it finds LABEL=mybig512 and mounts it
at `/mnt/bs1-backup` itself).  It refuses a SERIAL that isn't exactly one
NVMe disk with a `/dev/disk/by-id/nvme-*_SERIAL` link, or the disk the
drive is on.  Wait for `/run/bko-void-zbm/exit` (a dropped SSH connection
doesn't stop the run; the log is `/run/bko-void-zbm/install.log`): success
is `0` and the last line `BKO-VOID-ZBM-INSTALL-OK`.  A failed run can just
be run again: every run starts from the wipe.  The script leaves
`ZFSBootMenu` first in BootOrder and its number in
`/run/bko-void-zbm/zbm-entry` (`ZBM_ENTRY=NNNN`), for `efibootmgr -n` before
the reboot.

**The person types the passphrase once**, at the first boot: ZFSBootMenu
asks `Enter passphrase for 'zroot':`, the passphrase in
`keys/disk-passphrase`, on the US layout whatever the keys say (the release
image sets no keymap; on the A100's French keyboard, type where the US keys
would be).  Its menu then counts down and boots `zroot/ROOT/void`; the boot
environment's initramfs opens the pool with `/etc/zfs/zroot.key`.  Start
`backshot1 wait-host` once the passphrase is typed (it gives up after
10 min, and ZFSBootMenu waits for as long as nobody types).  Unplug a Void
live stick while there: preflight refuses extra USB disks.  Root and `m`
have no password (`*`): the way in is SSH, and `ssh -t ... root@thinkpad.local
passwd m` lets LightDM log `m` in.  `hostid` prints `00000000` on musl;
`/etc/hostid` (` 0c b1 ba 00`) and `spl_hostid` are what ZFS reads.  The
other boot environment boots from ZFSBootMenu's menu (`/etc/bko-be` names
it).

Then `backshot1 adopt` (family Z, below in "Unlocking from the drive"),
reboot, `wait-host` (nobody types now), `unlock` (it must say `drive`;
`typed` means the firmware booted `ZFSBootMenu (Backup)` or the removable
path `EFI/BOOT/BOOTX64.EFI`, neither of which adopt changes), `preflight`,
then `phase1` (it prints `RUN=NAME`) and
`cycle NAME 1 --erase-serial SERIAL`.  Passed 2026-11-19, run
`void-20261119-133257`: unattended (`UNLOCK=drive`), the round in 7 min,
115,399 files compared, 0 unexpected.

## Gentoo on the A100: systemd, LVM on LUKS2 (family S)

Gentoo on the A100 has the `gentoo-systemd` guest's shape, at real sizes:
UEFI, GPT, a 1 GiB ESP that is `/boot` (the kernels, dracut's initramfs,
GRUB in `EFI/gentoo` with an NVRAM entry `gentoo`, and `EFI/BOOT/BOOTX64.EFI`),
then LUKS2 (`cryptroot`, cryptsetup's defaults) holding LVM `vg0`: `root`
64 GiB ext4, `swap` 4 GiB, the rest free.  systemd, dracut's
`systemd-cryptsetup` opening the volume (crypttab's key field `none`,
`rd.luks.uuid` on GRUB's command line), `gentoo-kernel-bin` by
`installkernel[dracut,grub]`, binary packages from the 23.0 binhost.  So
`bs1-adopt` sets up family S (below, "Unlocking from the drive") with no
Gentoo code of its own.  The first Thinkpad's Gentoo (OpenRC, no LUKS,
2026-11-12) is the `gentoo` guest's shape; this one had never run on the
A100.  `testvm/realworld/gentoo-bootstrap.sh` installs it from the stick by
running the guest's own build script, `testvm/bootstrap/gentoo.sh`, with
`GENTOO_METAL=yes` (docs/design/gentoo.md, "Gentoo on the A100"): the stage3
from the drive, `linux-firmware` (r8169's `rtl_nic`, i915's), dbus and
avahi besides, and no key file anywhere: the volume asks for its passphrase
until adopt.

**The drive's `backshot1/gentoo/`** holds five files: the script, the
guest's build script beside it as `gentoo.sh`, and the systemd stage3 the
guest is built from with its signature and Gentoo's Release Engineering
key, which the script pins by SHA-256 and checks with `gpgv`.  Gentoo
prunes autobuilds after about two months; the three are in Chopin's
`/bkoscratch/rebuild-inputs/gentoo-systemd` (280,639,788, 525 and 10,155
bytes):

    8007e84c9190485a25f79f93d0c9438edbdc63d7b76d4462cb80ab9672af72ef  stage3-amd64-systemd-20261108T170107Z.tar.xz
    c77d8b28093f8bb05bb8ab9ce9cdf584828b5fe57511b16c5b433aff098edcb4  stage3-amd64-systemd-20261108T170107Z.tar.xz.asc
    af00969b4ed25c80615c023d6228721a02cf630383a4f959d3950a1815db333e  gentoo-release-key.gpg

With the drive mounted at `/mnt/bs1-backup` on the stick (`STICK` the
address `find-rescue` saved), from the Mac's checkout:

    ssh m@chopin.local 'tar -C /bkoscratch/rebuild-inputs/gentoo-systemd -cf - stage3-amd64-systemd-20261108T170107Z.tar.xz{,.asc} gentoo-release-key.gpg' |
      ssh backotter@STICK 'sudo mkdir -p /mnt/bs1-backup/backshot1/gentoo && sudo tar --no-same-owner -C /mnt/bs1-backup/backshot1/gentoo -xf -'
    COPYFILE_DISABLE=1 tar -cf - -C testvm/realworld gentoo-bootstrap.sh -C ../bootstrap gentoo.sh |
      ssh backotter@STICK 'sudo tar --no-same-owner -C /mnt/bs1-backup/backshot1/gentoo -xf - && cd /mnt/bs1-backup/backshot1/gentoo && sha256sum * && sync'

(`gentoo.sh` must be the same commit's as the script: the script refuses
one that doesn't know `GENTOO_METAL`.)  The keys are BackShot1's own
`backshot1/keys/` (`disk-passphrase`, `authorized_keys`, the fixed host
key).  Then on the stick, detached, since it takes a while (a source
build of systemd for `USE=cryptsetup`, and the binhost's packages over the
wired network: the tree, the binary packages and the few source builds'
distfiles are Gentoo's live ones, as the guest's are, since the binhost
follows the live tree):

    sudo bash /mnt/bs1-backup/backshot1/gentoo/gentoo-bootstrap.sh --detach S7XMNF0Y787118 thinkpad

It refuses a SERIAL that isn't exactly one whole disk, a USB disk, the
drive's or the stick's disk; a stick booted without UEFI or with Secure
Boot on (its GRUB is unsigned); a passphrase file that isn't 8 to 512
printable ASCII bytes with no newline; inputs whose SHA-256 or signature
don't match; and no DNS for `distfiles.gentoo.org`.  Everything it mounts
is under `/run/bko-gentoo/mnt` (a private tmpfs), never `/mnt`.  The
passphrase is read only by `cryptsetup --key-file` from the drive.  Wait
for `/run/bko-gentoo/exit` (a dropped SSH connection doesn't stop the
run): success is `0` and the last line of `/run/bko-gentoo/install.log`
`BKO-GENTOO-INSTALL-OK`; the log is also copied to
`backshot1/gentoo/install-DATE-exitRC.log` (before the exit file is
written: once it is there, the drive can come out).  One run at a time:
a second one is refused while the first holds `/run/bko-gentoo/lock`.  A
failed run can just be run again: every run starts from the wipe.  It
deletes NVRAM entries labelled `gentoo` or naming the wiped disk's old
partitions, and leaves `gentoo` first in BootOrder.  Those partitions'
GUIDs are kept in `backshot1/gentoo/old-partuuids` from before the wipe
until a run gets that far, so a run that wiped the disk and then failed
still leaves the earlier system's entries to the next run to delete.

**The person types the passphrase once**, at the first boot: systemd asks
`Please enter passphrase for disk cryptroot` on the screen, the passphrase
in `keys/disk-passphrase`, on the US layout (no keymap is set: on the
A100's French keyboard, type where the US keys would be).  Then `backshot1
wait-host`, `adopt` (family S: crypttab's key field the drive's key, a
dracut drop-in, the initramfs remade), reboot with `mybig512` plugged in
and nothing typed, `unlock` (it must say `drive`), `preflight`, `phase1`
(it prints `RUN=NAME`) and `cycle NAME 1 --erase-serial S7XMNF0Y787118`.
Root's password is locked (`*`): the way in is SSH by key.

`testvm/realworld/gentoo-bootstrap-qemu.sh TEST_IMG INPUTS_DIR WORK_DIR`
rehearses all of it in QEMU: the drive (a fake XFS `mybig512`) staged over
SSH as above, the same command line with the rehearsal's serial, a watcher
for the passphrase in any process's command line or environment, the
teardown, then a typed boot (`bs1-adopt` twice), a boot with the drive and
nothing typed (`UNLOCK=drive`) and one without it (asked after the 30 s
wait).  PASS on Chopin 2026-11-27 (rescue image `20261126.d66770a`'s test
build): the install took 1105 s with 4 CPUs (gentoo.sh 1073 s, the
systemd build most of it; the tree of 2026-11-27, gentoo-kernel-bin
6.18.65), the typed boot answered SSH 41 s after power-on, the drive boot
20 s with nothing typed (`UNLOCK=drive`), the boot without the drive 71 s
(asked after the wait).  Again PASS after the review's fixes, the same
day on Chopin, with the new checks: a second `--detach` refused while
the first ran and the log left whole, a seeded `oldos` entry (its GUID
only in `old-partuuids`) deleted and the file dropped, no scratch left in
`/run/bko-gentoo` or `/tmp` (install 1162 s).  PASS a third time at
94a1522 on Cinqcent (placed by `pick-host`; the disk in `DISK_DIR` on
`/bkoscratch`, the rest under `cmp-run.sh --reserve 4 10`), 59 checks: the
install 1071 s (gentoo.sh 1038 s, tree 2026-11-26, gentoo-kernel-bin
6.18.65, nothing source-built outside the allowlist), the same 41 s, 20 s
and 71 s boots.  Then a whole BackShot1 round on that same rehearsed disk:
booted once with the drive to run `bs1-adopt --luks-off` (crypttab back
to `none`, the initramfs's too), then given to `bs1-qemu.py --luks-typed
--luks-monitor` as a GUEST_DIR (the disk, the rehearsal's client key as
`id_ed25519`, its passphrase as `luks-passphrase`, its `vars.fd` as
`uefi-vars.fd` with `--no-secure-boot` in `vm-run-options`, so the source
boots by the `gentoo` entry as the A100 will): run
`gentoo-2.18-20261127-200627`, 36 checks ok, the backup 4.3 GiB of 236,219
files, `bs1-verify` PASS (273,211 files compared, nothing unexpected),
the restored system booted from NVRAM and unlocked by the drive
(`UNLOCK=drive`), and asked without it.  Not yet run on the A100.

## BigLinux: Timeshift's snapshots and systemd-swap

BigLinux (Manjaro stable underneath; docs/design/biglinux.md) installs with
Calamares's defaults: btrfs `@` and `@home` with `compress-force=zstd`,
GRUB at `EFI/BigLinux` and the fallback loader, a busybox mkinitcpio, and
no swap partition: systemd-swap makes a top-level `@swapfile` at
`/swapfile`.  Timeshift keeps btrfs snapshots at the top of the file
system (`timeshift-btrfs/snapshots`), outside every mount, so they are not
in the backup: the restore leaves them out with a note, and Timeshift's
hourly check takes a new one afterwards.  `bs1-manifest`'s `EXPECTED` has
`swapfile/*`, systemd-swap's files, written anew at every boot.  `bs1-adopt`
needs nothing new (BigLinux has sshd and avahi), and the run is unattended.
Rehearsed 2026-11-14 with `bs1-qemu.py --virtio` on `biglinux`, PASS on
Chopin and Cinqcent.  The Thinkpad run isn't done yet: its install and
staging steps are in docs/design/biglinux.md ("What BL6's rehearsals
found", "For the real run on the Thinkpad").

## Solus: clr-boot-manager's ESP at /boot, out of fstab

Solus 4.9 (docs/design/solus.md) boots shim and systemd-boot under
non-standard names in `EFI/com.solus-project`, all of them, with the
kernels and initrds, put on the ESP by clr-boot-manager.  The ESP is
`/boot` itself and isn't in fstab: clr-boot-manager mounts it only while it
updates, and `bko-holt dump` refuses while it isn't mounted.  So
`bs1-backup` (the preflight and phase 1) and `verify` first run
`clr-boot-manager mount-boot`, the step a Solus user takes, and stop if
`/boot` isn't then on the internal disk.  The restore makes only the NVRAM
entry ("Solus Linux Bootloader"): no bootctl, no clr-boot-manager, no
initramfs.  So `verify` holds the whole ESP to the baseline's bytes
(`bs1-manifest`'s `SOLUS_KEPT`, as HoltTest1's judge.py does), all but
`loader/random-seed`, which systemd-boot refreshes at every boot: it only has
to be there, a regular file (`SOLUS_PRESENT`).  It fails if
the baseline lacks the ESP's loaders, a kernel, an initrd or an entry.  The
package list is eopkg's database, `/var/lib/eopkg/package/NAME-VER-REL`.
It goes on the Thinkpad from Solus's own GUI installer (ext4 or F2FS, no LUKS:
`bs1-luks` has no arm for Solus's prebuilt initrd, which nothing on the
machine rebuilds).
`bs1-adopt` installs `openssh-server` and `avahi` with `eopkg it` (after
`eopkg ur` and a whole-system `eopkg up` if that fails: Solus takes no
partial update), and first writes `/etc/systemd/system-preset/50-backshot1.preset`
enabling sshd and avahi-daemon, since Solus's presets end with `disable *`
and usysconf applies them again after every eopkg transaction.  Plumbed
2026-11-15.  The first Thinkpad run (solus-4.9-20261117-202222, F2FS root)
restored and booted, and its verify failed three checks, all the harness's:
- Solus 4.9 ships without diffutils, so `verify`'s swap and package checks
  had no `cmp`: they compare with `same_as` (Python) now, and `bs1-adopt`
  installs `diffutils` when `cmp` is missing.
- `eopkg up` leaves `/var/lib/qol-assist/trigger`, and
  qol-assist-migration.service ran `qol-assist migrate` at the restored
  system's first boot (`usermod`, `groupmod`), changing `etc/group`,
  `etc/gshadow` and their `-` backups: `bs1-adopt` now runs the migration
  itself after its last eopkg transaction and removes the trigger, and
  `bs1-manifest` expects those four files changed, and the trigger gone,
  when the baseline lists the trigger (`QOL_EXPECTED`).
- The baseline had no `loader/random-seed`, and the restored boot wrote
  one: a `SOLUS_PRESENT` path added as a regular file is expected; one
  missing after, or not a regular file, still fails.

With those fixes the same restore's re-verify passed on 2026-11-17
(341,494 files compared, none unexpected), unattended, no LUKS.

The `bs1-qemu.py --virtio` rehearsal on `solus49` hasn't been done.  Its
firmware has no Secure Boot (OVMF_CODE_4M), so `solus49`'s `--mok` isn't
used there: the restored ESP's boot under Secure Boot is HoltTest1's check
(S5).

## ABRoot: Vanilla OS's two roots

Vanilla OS 3 (docs/design/vanillaos.md) keeps two btrfs roots on LVM thin
volumes (`vos-a`, `vos-b`), both roots' kernels in an `init` LV, and a static
master `grub.cfg` on `/boot` whose literal `set default=N` picks the root.
`/` is the present root, read-only, with an overlay on `/etc` whose upper is
in `/var/lib/abroot/etc/LABEL`; the other root and the `init` LV are mounted
by nothing.  BackShot1 knows it by `/usr/share/abroot/abroot.json`
(`is_abroot` in bs1-common).  Plumbed on 2026-11-15 (V11).  **Thinkpad
BackShot1 PASS 2026-11-17** (run vanilla-3.0-20261117-132713: LUKS `/var`,
attended; 563,666 files; 0 unexpected).  That run's fixes: verify drops its
tree before `abroot status` (a second mount of `/` makes abroot report no
present root), counts a boot's `vso upgrade` that found no updates as quiet,
and expects `/var/vlogs`, first-setup's command list and abroot's empty
package lists.  No `bs1-qemu.py` rehearsal on `vanilla3` yet.

- **The tree is the layout's six paths** (`storage.backup_sources`): `/`
  (a bind of the present root, so its raw `/etc`, the image's), `/boot`,
  `/boot/efi`, `/var`, and `/boot/init` and `/part-future`, which phase 1
  mounts read-only under `/run/bs1-ro` as their mount hints say (`ro,noload`
  for the ext4 `init` LV, `ro,rescue=nologreplay` for the btrfs root: no
  journal replayed) and binds in; `drop_tree` unmounts them.  The coverage
  check counts those two as mounted.
- **Preflight refuses** (and so does phase 1, `bs1-abroot preflight`) when an
  ABRoot operation is running (`/run/abroot/operation.lock`), when the
  master grub.cfg boots another root than the running one (an upgrade waits
  for a reboot, or the other entry was picked by hand: reboot into the
  default first), when the layout isn't the two-root thin one `bko-holt
  load` restores, or when a root or the `init` LV is missing from
  `backup_sources`.  Phase 1 and `verify` hold `vanilla-updates.timer` (vso
  upgrade, then `abroot upgrade` into the other root) for their boot, as
  ostree's updaters; disable it for the whole run (`systemctl disable --now
  vanilla-updates.timer`) and turn it back on once `verify` has passed.
  Phase 1 fails if the master grub.cfg, `grub.cfg.future` or the lock moved
  while it ran.
- **The baseline gains `abroot.txt`** (`bs1-abroot record`): the present
  and other root, the master grub.cfg's default and entries, each root's
  filesystem UUID, LV and ABImage digest, its `abroot.cfg`'s kernel, initrd
  and `root=`, each file in `init/LABEL/` by sha256, the master grub.cfg
  and `grub.cfg.future` by sha256, abroot.json (merged, and each root's
  own), the kargs, each root's hostname and `/etc/bko-root` (the test
  guest's mark, if any), and the command line.
- **What verify adds:** the manifest maps only the present root's upper to
  `/etc` (so a boot's `/etc` changes there are judged as anywhere else, and
  mount-sys's rewrite of its `fstab` must keep the content); the other
  root, its upper and work dir, and the `init` LV get no exceptions; the
  master grub.cfg, `grub.cfg.future`, `EFI/vanilla` and `EFI/BOOT` must
  come back byte for byte (`ABROOT_KEPT`: load regenerates nothing on
  ABRoot), though grubenv may change; `/home`, `/root` and `/mnt`, links
  into `/var`, are judged by those names.  Then every line of `abroot.txt`
  against the baseline's, and `bs1-abroot boot-check`: this boot is the
  default root, `abroot status` calls it present, its kernel and `root=` are
  its `abroot.cfg`'s, its hostname, mark and `/abimage.abr` are its own, `/`
  is btrfs read-only and `/etc` its overlay.
- **The other root boots too.**  After `verify` passes, pick the master
  grub.cfg's other entry ("Previous State (A)" after an upgrade into B) in
  GRUB's menu at the laptop's console, then `backshot1 check-boot NAME`:
  `bs1-abroot boot-check` against that root's record (`BOOT: other`).
  Then reboot: GRUB starts the default again, and `check-boot` must say
  `BOOT: default`.  `bs1-qemu.py` does both on a guest, the entry typed on
  GRUB's serial menu by `testvm/vanilla/console-keys.py`.  A fresh install's
  other root is empty until its first `abroot upgrade`: then there's no other
  root to boot (`bs1-qemu.py` skips it).  `backshot1 cycle` refuses an
  ABRoot run, as an ostree one: that visit needs the console.

What a Thinkpad run still lacks (not built):

- **An unattended install.**  Vanilla OS installs from its live ISO's GUI
  (vanilla-installer handing a recipe to Albius); there's no
  `vanilla-bootstrap.sh` like PikaOS's or neon's.  `testvm/make-vanilla.sh`
  and `testvm/vanilla/install.sh` run Albius unattended in QEMU only (a
  systemd unit given to the live system as an SMBIOS credential, the
  installer's own recipe for `/dev/vda`); a Thinkpad version would run
  Albius against the disk with the serial (`/dev/nvme0n1` in the recipe),
  add the controller's key and the fixed host key, enable `ssh.service`, and
  then run `abroot upgrade --force` and reboot once, so both roots are filled
  (V1: the fresh install leaves B empty).  By hand in the GUI, then
  `abroot upgrade` and a reboot, works too.
- **bs1-adopt on the other root.**  It needs nothing installed on Vanilla
  (sshd, avahi-daemon, Python and efibootmgr are in the image; there's no
  `apt-get`, so a missing tool would stop it, to be layered with `abroot pkg
  add` and `abroot pkg apply` and a reboot).  What it writes in `/etc` lands
  in the present root's upper only, so the other root keeps its own
  hostname and host key: as with ostree's rollback, the controller refuses
  its host key, and `check-boot` for it is from the console, or after
  bs1-adopt run once more in that root's boot.  `/root` is on `/var`, so the
  controller's key is in both.
- **LUKS `/var`** (the installer's encryption option): `abroot unlock-var`
  asks through Plymouth from the init wrapper, not the initramfs, so none of
  bs1-luks's unlock-from-drive families applies (its crypttab line finds
  the volume already open), and the hook is in the read-only root image.
  bs1-adopt records it as family A, attended (2026-11-17, Thinkpad:
  adopt and preflight PASS, `UNLOCK=attended`); no `vanilla3-luks` guest.

## Proxmox VE: proxmox-boot-tool's ESPs

Proxmox VE 9 installed on ZFS (docs/design/proxmox.md) boots systemd-boot
from ESPs that proxmox-boot-tool keeps: they aren't in fstab, nothing mounts
them but proxmox-boot-tool while it works, and
`/etc/kernel/proxmox-boot-uuids` lists them by vfat volume ID.  So the
coverage check takes a vfat partition on the disk whose volume ID is in
that list as accounted for, and says so; any other unmounted filesystem
still stops it.  They aren't backed up: `bko-holt dump` records them in
`boot.proxmox_boot`, and the preflight and phase 1 stop unless it lists the
same ones, since the restore formats each with its volume ID, then runs
`proxmox-boot-tool init` on each and one `refresh` in the chroot, which put
systemd-boot, the kernels, initrds and loader entries back from `/boot`.
NVRAM: `init`'s `bootctl install` makes or keeps "Linux Boot Manager" for
the restored ESP (load makes no entry of its own for it), and the
partitions keep their GPT GUIDs, so the entry still names it and BootNext
picks it; with none, the one-shot "BKO restored" for `EFI/BOOT/BOOTX64.EFI`,
which `init` writes too.

`/etc/pve` is pmxcfs, a FUSE view of `/var/lib/pve-cluster/config.db`;
that file and its WAL are backed up with `/` while pmxcfs runs, and phase 1
stops (run it again) if pmxcfs checkpointed `config.db` while rustic read
it, since the two copies might then not agree.  The tree's binds show `/etc/pve` as the empty directory under the mount, as
`/var/lib/lxcfs` (lxcfs) too.  `/root/.ssh/authorized_keys` is pmxcfs's
link to `/etc/pve/priv/authorized_keys`, so the controller's key comes back
with `config.db`.  The ZFS datasets (`/`, `/rpool`, `/rpool/ROOT`,
`/rpool/data`, `/var/lib/vz`) are in the tree; zvols for VM disks would
come back empty (vzdump's to back up).  `bs1-manifest` expects
`etc/issue` (pvebanner writes it at every boot, from `/etc/hosts`' address),
`etc/kernel/proxmox-boot-uuids` and `etc/apt/apt.conf.d/76pveconf`
(rewritten by `init` and `refresh`) with new mtimes but the same bytes
(`PROXMOX_REMADE`, for a baseline with `usr/bin/pvebanner`).  The Thinkpad
(2026-11-19: Proxmox VE 9.2.2, ZFS RAID0 on the NVMe, static address in
`/etc/network/interfaces` on `vmbr0`, no LUKS, no swap) hasn't been run yet.

## TrueNAS: its ESP, the boot environments and the config database

TrueNAS Community Edition 25.10 (docs/design/truenas.md) is Debian 12 to
os-release; BackShot1 knows it by `/etc/version` (the release, `25.10.8`)
and `midclt` (`is_truenas` in bs1-common).  Its installer makes three
partitions on the boot disk, a BIOS boot partition, an ESP and `boot-pool`,
and the ESP is in no fstab: only the installer's `grub-install` mounts it.
So on TrueNAS the coverage check takes a vfat partition of the ESP type as
accounted for, and says so; any other unmounted filesystem still stops it,
and so does that ESP anywhere but on TrueNAS.  It isn't backed up:
`bko-holt dump` records it in `boot.truenas.esps`, and the preflight and
phase 1 stop (`truenas_check` in bs1-backup) unless the dump is of TrueNAS
25.10 (`os.product`, the one release load restores), lists each of the
disk's ESPs with the same volume ID, and read each one, holding nothing but
what the restore writes again: load formats it with that volume ID and
label, and its `truenas` boot arm runs `grub-install` for i386-pc and
x86_64-efi (`--bootloader-id=debian --no-nvram`: shim, MokManager, the
fallback, GRUB and its `grub.cfg` stub in `EFI/debian`), copies GRUB to
`EFI/boot/bootx64.efi` and runs `update-grub`.  A file on it besides those
(another loader's, say) stops the preflight, since the restore would lose
it.  NVRAM: load remakes the source's "debian" entry for the ESP, which
keeps its GPT GUID, so BootNext picks it.

The tree is the mounts, as elsewhere: the booted boot environment
`boot-pool/ROOT/V` (`readonly=on`, mounted read-only at `/`), its 15
children (canmount=noauto, mounted by TrueNAS's initramfs), the legacy
`boot-pool/grub` at `/boot/grub` and `boot-pool/.system` with its children
at `/var/db/system`.  `.system/cores` is mounted twice, at
`/var/db/system/cores` and `/var/lib/systemd/coredump`; it is in the tree
once, at the first, as the dump lists it and load mounts it
(`internal_mounts`), and the other path is `var/lib`'s own directory.
Load holds `readonly=on` back until its boot stage is done.  An inactive
boot environment (one per update, `boot-pool/ROOT/OLD`) is mounted by
nothing: the dump lists it as a source with a read-only mount hint, which
only ABRoot's tree mounts, so the preflight and phase 1 stop at one
(`unhinted_check`) rather than let it come back empty.  Phase 1
fails (run it again) if the middleware wrote `/data/freenas-v1.db`, the
whole configuration, while rustic read it.

The middleware writes much of `/etc` from that database at every boot
(`etc.generate_checkpoint`: `/etc/hostname`, `/etc/ssh`'s host keys and
`sshd_config`, which services systemd starts), so whatever is written
there by hand is gone at the next one.  The first round trip
(debian-12-20261125-170305) came back healthy but as `truenas`, with the
install's own host key and `ssh.service` disabled, since adopt had written
`/etc` itself, and the source would have lost them at its next reboot too.
So on TrueNAS bs1-adopt puts them in the database, by the middleware's own
calls: the hostname by `network.configuration.update`; the fixed host key
by `ssh.save_keys`, which reads `/etc/ssh`'s keys into `services_ssh`'s
columns (base64; the private key encrypted with `/data/pwenc_secret`), then
`etc.generate ssh`, which writes them and `sshd_config` back from there (the
RSA and ECDSA keys the database has come back with them); `ssh.service` at
boot by `service.update ssh '{"enable": true}'`; mDNS by
`service_announcement`'s `mdns`.  It adds nothing to `sshd_config`: the
middleware's lets root in by key (`PermitRootLogin without-password`).
root's `authorized_keys` and the sleep drop-in are files the middleware
doesn't write, as elsewhere.  adopt is idempotent: a second run says
`unchanged` for each.  It writes the boot it last changed the database in
to `/etc/backshot1/truenas-adopt`, and the preflight and phase 1 stop
(`truenas_adopted`) until a later boot, and while the database lacks any of
those settings: so reboot after adopt (`systemctl reboot`).  That reboot
shows they stay, makes the baseline what a boot makes of the database (as
the restored system's first boot is), and finishes a fresh install's
`/etc`: its first boot couldn't generate the nfsd group (no
`system.globalid` yet), so `/etc/nfs.conf`, `exports`, `idmapd.conf` and
`default/rpcbind` came only at the second.

What a boot does to the files, from the first round trip and two plain
reboots after adopt (`TRUENAS_*` in bs1-manifest, on a baseline with
`midclt` and `/data/freenas-v1.db`): written again with the same bytes, new
mtimes (`TRUENAS_REMADE`), `user-services.json`, the default certificate and
key (from the database's `system_certificate` row, which came back the
same), `grub.d/truenas.cfg`, the hostname, the `localtime` and
`vconsole.conf` links, `/etc/nut`, `resolv.conf.bak`, `ca-certificates.crt`,
the fixed ed25519 host key, the system dataset's `nfs/rmtab` and samba's
`netlogon_creds_cli.tdb`.  With other bytes (`TRUENAS_CHANGED`, each still a
regular file of the same mode and owner): `cron.d/middlewared`, whose
`update.download` gets a random minute and hour each time, `netdata.conf`,
whose disk space netdata sizes by the metrics it counts at that boot, and
`freenas-v1.db` itself, whose pages a boot writes again with the same rows.
The running system's own, made, changed or gone (`TRUENAS_EXPECTED`): the
audit databases' WAL and shared memory and syslog-ng's queues in `/audit`,
and netdata's metrics under `var/db/system/netdata`.  The audit databases
themselves and the rest of `/etc` and the system dataset are held.

So `freenas-v1.db` is compared by its rows instead: phase 1 records them in
`baseline/truenas-db.txt` (`bs1-manifest --db-dump`: each table's rows as
JSON and the schema, sorted, so the same rows read the same however the
pages lie), between the stats that show the middleware didn't write it
during the backup; `verify` dumps the restored one and compares
(`--db-compare`, `truenas-db-compare.txt`), failing on any row or schema
entry only one side has, and on a baseline without the record.  No table is
left out (`TRUENAS_DB_VOLATILE`): neither the restore's first boot nor two
plain reboots changed a row.

The Thinkpad (2026-11-25: TrueNAS SCALE 25.10.8, one boot environment, no
data pool, no LUKS, UEFI): debian-12-20261125-170305 restored and booted
(phase 2 PASS) but failed `wait-host` and a verify by address, on the
hostname and the paths above that adopt had written.  The restored system,
adopted again and rebooted, answers as `thinkpad.local` with the fixed key,
and passes the preflight.

## Keeping a library of backups

Each backup is a run of its own, named after the distro and the time:
`backshot1/runs/ubuntu-26.10-20261108-204147/`, holding its repository, layout,
password and baseline.  `backshot1 list` shows them and how each last restored.

NAME is a whole run's name, or a distro (`ubuntu-26.10`, `debian-13`) for its
newest backup.  A partial name that fits two distros is refused.  Any run can be
restored any number of times, in any order, without reinstalling anything: its
system already has BackShot1's keys.  Each attempt logs to
`runs/RUN/restores/STAMP/`, and `verify` checks only the newest.

An old backup tests the restore and the rescue image, not `dump` or the backup, so
take a fresh one when those change.  The firmware's boot list is the one thing
that carries over from one restore to the next.

## Unlocking from the drive

`bs1-adopt` makes a LUKS install unlock at boot with
`backshot1/keys/disk-passphrase` from the drive labelled `BACKUP_LABEL`
(`bs1-luks`; docs/design/bs1-luks-unattended.md).  If the drive isn't there
within 30 s, or the key doesn't fit, the usual prompt follows.  The settings
are in the installed system, so in the backup: the restore's boot stage
remakes the initramfs from them, and the restored system unlocks itself too
(on NixOS by UEFI the restore remakes nothing: the restored system boots the
initrd the backed-up ESP has, which already has them; on AerynOS the
backed-up ESP's boot entries carry family B's command line, and its
`cmdline.d` file comes back with `/etc` for the next `moss boot sync`).  The drive may be
ext4 or xfs (the Thinkpad's `mybig512` is xfs).
How depends on what opens the volume:

| Family | Who | How |
|---|---|---|
| S | systemd in the initramfs: dracut (Fedora, EndeavourOS, openSUSE, Gentoo systemd, Ubuntu 26.04), mkinitcpio's `sd-encrypt`; and any volume the booted system opens under systemd; Ubuntu's ZFS keystore on dracut (26.10's "ZFS + encryption", volume `K`) | crypttab's key field `/backshot1/keys/disk-passphrase:LABEL=mybig512`, option `keyfile-timeout=30s`; `/etc/crypttab.initramfs` for `sd-encrypt`; a dracut or mkinitcpio drop-in adds the USB modules, ext4 and the drive's own file system.  On mkinitcpio's unified kernel images (presets with `default_uki`), `rd.luks.name`/`uuid`/`options` in the command line mkinitcpio bakes into them (`/etc/kernel/cmdline`, `/etc/cmdline.d/*.conf`) become each volume's `crypttab.initramfs` line and are taken out of that file, in the same `mkinitcpio -P` (below).  Ubuntu's keystore: a dracut module of BackShot1's, `/usr/lib/dracut/modules.d/99backshot1`, puts the drive's key where `systemd-cryptsetup` looks before it asks (below) |
| D | initramfs-tools: Debian, Ubuntu up to 25.04, Mint, MX, Chimera; and on sysvinit (MX, Devuan) any volume Debian's `cryptdisks-early` or `cryptdisks` init script opens after the initramfs | crypttab option `keyscript=/usr/local/lib/backshot1/unlock-key` (`bs1-unlock-key`), the key field left as it was (`none`, or decrypt_keyctl's cache ID); it asks with askpass when the drive's key can't be read, and once a boot found no drive the later volumes ask without waiting again.  Debian's `keyscript=decrypt_keyctl` (MX's installer) is replaced by it, the line's other options kept, so a typed boot asks for each volume instead of once for all; any other keyscript is attended.  A late volume needs the keyscript on the root file system (`/usr/local` not a mount of its own) and `CRYPTDISKS_ENABLE` not off (any value starting `N` or `n`, as the init scripts read it); OpenRC's `dmcrypt`, runit and dinit opening one stay attended |
| E | mkinitcpio's busybox `encrypt`: Arch, Manjaro, Omarchy | a `bs1key` hook, put right before `encrypt` by `/etc/mkinitcpio.conf.d/zz-backshot1.conf`, writes `/crypto_keyfile.bin` only if that key opens the volume |
| N | NixOS (systemd or scripted stage 1) | `/etc/nixos/backshot1-luks.nix`, imported by `backshot1.nix` while it exists, sets each typed `boot.initrd.luks.devices` volume's `keyFile`: systemd stage 1 the same `PATH:LABEL=` form and `keyFileTimeout = 30`; scripted stage 1 a copy its `preOpenCommands` take off the drive (mounted read-only, `norecovery` for xfs), with `fallbackToPassword`; then `nixos-rebuild switch` |
| B | AerynOS: systemd in the initramfs that comes prebuilt with the kernel (it reads no crypttab of the system's), the kernel command line written by blsforme from `/etc/kernel/cmdline.d` | `/etc/kernel/cmdline.d/90-backshot1-luks.cmdline` gives each volume the initramfs opens `rd.luks.uuid=UUID rd.luks.key=UUID=/backshot1/keys/disk-passphrase:LABEL=mybig512 rd.luks.options=UUID=OPTIONS,keyfile-timeout=30s`, OPTIONS those the other `cmdline.d` files already give it (one `rd.luks.options=` for the volume, since the last wins); then `moss boot sync`, and every boot entry on the ESP must carry the key and those options as the last of each; a volume the booted system opens is S's |
| P | booster (PikaOS), its command line rEFInd's `/boot/refind_linux.conf` | booster reads a key file only from inside its image, once, as the volume appears, before the drive is there; so each `refind_linux.conf` line that opens the volume gets `rdinit=/usr/local/lib/backshot1/booster-preinit rd.luks.key=UUID=/backshot1-key.UUID` (and `booster.log=warning` on a `quiet` line).  The preinit makes that key file a FIFO, starts `bs1-unlock-key --booster-run` in the background and execs booster's `/init`; the helper writes the drive's key into the FIFO booster is waiting on (`/run/backshot1-unlock`: `NAME drive`), or ends it empty after 30 s without the drive, and booster asks; no FIFO is ever left for no one to write.  `/etc/booster.yaml`'s `extra_files` gain busybox, the preinit, the script and the record, its `modules_force_load` the USB modules and the drive's file system; each kernel's image is remade (`booster build`) and checked before `refind_linux.conf` is written (below) |
| Z | ZFSBootMenu with ZFS native encryption (passphrase) and no LUKS: `void-zbm-enc`, the ZFSBootMenu guide's install | ZFSBootMenu hooks on the ESP in `EFI/ZBM/backshot1/` (a `load-key.d` hook made from `bs1-unlock-key --zfs`, and a `boot-sel.d` hook), named by `zbm.hookroot=UUID=ESP//EFI/ZBM/backshot1 zbm.wait_for=UUID=ESP` added to `EFI/ZBM/VMLINUZ.EFI`'s embedded command line (below) |
| G | Guix System: GRUB opens /boot's and the store's LUKS1 volume, then the initrd opens it again (`guix15-luks`, Guix's installer's encrypted layout) | `/etc/backshot1/luks.scm`, applied by adopt's `system.scm` while it exists: a boot loader inheriting `grub-efi-bootloader` that writes `EFI/Guix/grubx64.efi` again with BackShot1's script (`cryptomount -k` with the drive's key, else the prompt), `extra-initrd` with the key in a cpio and each volume's `#:key-file`; then `guix system reconfigure` (below) |
| A | non-systemd dracut, Alpine's mkinitfs, ostree; ZFS native encryption not booted by ZFSBootMenu (no record is written for it); GRUB reading /boot from LUKS, whatever the initramfs is, but on Guix System (G; below) | nothing: the run is attended |

A volume that already opens with no one typing (a key file of its own in the
initramfs, TPM2, FIDO2; on B an `rd.luks.key=` of its own in another
`cmdline.d` file; on P one in `refind_linux.conf`) is left alone (`own`).  The boot loader is never touched,
nor the kernel command line, but for B's `cmdline.d` file and the entries
`moss boot sync` writes from it, S's unified images and P's
`refind_linux.conf` (below); another
`cmdline.d` file is never edited (adopt warns of one with an `rd.luks.key=`:
`--luks-off` leaves it).  Adopt
checks the key with `cryptsetup --test-passphrase` before changing
anything, makes the drive's key file mode 600 (systemd-cryptsetup warns of
one anyone can read; `backshot1 stage` does too), saves each file it
changes in `/etc/backshot1/orig/`, records what it did in
`/etc/backshot1/luks-unlock`, remakes the initramfs as the restore does (B:
the boot entries) and checks the new one; if that fails, it puts everything
back.  Run again, it changes nothing.

**S on mkinitcpio's unified kernel images** (Arch with `sd-encrypt`,
`default_uki` in `/etc/mkinitcpio.d/linux.preset`, systemd-boot; the
Thinkpad, 2026-11-18): `rd.luks.options=` on the command line overrides
crypttab's options, `keyfile-timeout` with them, so before this adopt said
family A.  When those arguments come from the files mkinitcpio bakes into
the images' `.cmdline` (`/etc/kernel/cmdline`, else
`/usr/lib/kernel/cmdline`, then every `/etc/cmdline.d/*.conf`), adopt
instead:

- writes each volume's `/etc/crypttab.initramfs` line: its open mapping
  name (so `root=/dev/mapper/NAME` still matches), `UUID=`, the drive's
  key, and the options the command line put in force with
  `keyfile-timeout=30s` (`keyfile-offset`, `-size` and `-erase` dropped);
- takes `rd.luks.name`, `rd.luks.uuid` and `rd.luks.options` (and `luks.`
  ones) out of `/etc/kernel/cmdline` or the `/etc/cmdline.d` file they're
  in, everything else byte for byte, the original in `/etc/backshot1/orig/`
  (`--luks-off` puts it back exactly and remakes the images);
- runs `mkinitcpio -P` once for both, and checks each remade image: its
  initramfs has the crypttab line, and its `.cmdline` (read from the PE
  section) has no `rd.luks.options`, `.key` or `.crypttab`, nor any
  `rd.luks` argument left.  Otherwise it puts everything back.

The running `/proc/cmdline` keeps the old arguments until the next boot,
so adopt decides from the files (and from its own copy of them in
`orig/`): run again before the reboot, it changes nothing.  The reboot
preflight wants is the same as ever.  It stays family A (nothing changes)
when the arguments come from the boot loader instead (a systemd-boot type 1
entry's `options`, GRUB's `GRUB_CMDLINE_LINUX`, Limine's or rEFInd's
config: on `/proc/cmdline` but in none of those files), from a preset's
own command line (`NAME_cmdline`, `ALL_cmdline`, `--cmdline`), from
`/usr/lib/kernel/cmdline` or a link, when this boot didn't come through a
unified image (no systemd-stub `StubInfo` EFI variable), and for anything
it can't move into crypttab as it is: TPM2, FIDO2 or PKCS#11 options, an
`rd.luks.key=` that isn't the drive's, `rd.luks.crypttab=`, `rd.luks.data=`,
a UUID that isn't a typed volume opened early, a name that isn't the one
the volume is open as, or a `crypttab.initramfs` line the command line
didn't name (it would open in the initramfs once nothing is named).  The
boot loader's own entries stay out of reach because the remake that bakes
crypttab into the initramfs (`mkinitcpio -P`, as the restore's boot stage
runs it) doesn't rewrite them, so the two couldn't land in the same step;
a boot whose command line no longer names the volume, with no crypttab
line for it either, would wait for its root until the emergency shell.

**S, Ubuntu's ZFS keystore** (Ubuntu 26.10's "ZFS + encryption", the
Thinkpad 2026-11-19; volume HOW `K` in the record).  `rpool` is natively
encrypted with a raw key, `keylocation=file:///run/keystore/rpool/system.key`,
and that file is on ext4 in a LUKS2 volume on the zvol `rpool/keystore`.
Nothing in crypttab or on the command line opens it: Ubuntu's dracut `zfs`
module does (zfs-linux's `4001-dracut-Open-and-mount-luks-keystore.patch`,
LP #2070066), in its pre-mount hook `90-zfs-load-key.sh`, once the pool is
imported, by `systemd-cryptsetup attach keystore-rpool
/dev/zvol/rpool/keystore` with no key file.  Given none, systemd-cryptsetup
first tries `/run/cryptsetup-keys.d/keystore-rpool.key` and asks only when
that's missing or doesn't fit.  Adopt:

- checks that the volume is `keystore-POOL` on the zvol `POOL/keystore`, that
  `POOL` is an encryption root keyed from `/run/keystore/POOL/` with `/`
  under it, that the initramfs is dracut with systemd, and that the `zfs`
  dracut module's `zfs-load-key.sh` opens it with `systemd-cryptsetup attach`;
  anything else is family A, as before;
- writes `/usr/lib/dracut/modules.d/99backshot1/` (module `backshot1`, asked
  for by `add_dracutmodules` in `/etc/dracut.conf.d/90-backshot1.conf` with
  the USB modules and the drive's file system) and
  `/usr/local/lib/backshot1/unlock-key`.  Its pre-mount hook
  `85-backshot1-keystore.sh` (`bs1-unlock-key --keystore`) waits up to 30 s
  for the drive, mounts it read-only and copies the key to
  `/run/cryptsetup-keys.d/keystore-rpool.key`; `95-backshot1-keystore-done.sh`
  (`--keystore-done`), after the zfs module's, marks `keystore-rpool drive`
  in `/run/backshot1-unlock` when the volume is open and that key opens it
  (else `typed`), and removes the key;
- remakes the initramfs and checks each image has the three hooks in that
  order, `systemd-cryptsetup`, the script and the record.

crypttab, the pool and the boot loader are not touched.  The restore makes
the keystore with the drive's key (`--key-file keystore-rpool=...`), and its
boot stage remakes the initramfs with the module, so the restored system
unlocks by itself too.  The random-key swap needs nothing.

**Z, ZFSBootMenu** (ZFS native encryption with a passphrase, no LUKS; the
ZFSBootMenu guide's UEFI install, `EFI/ZBM/VMLINUZ.EFI` with
`VMLINUZ-BACKUP.EFI` beside it).  ZFSBootMenu asks for the boot
environment's encryption root, then kexecs the boot environment's kernel,
whose own initramfs loads the key from the key file `keylocation=file://`
names inside it (the guide's `/etc/zfs/zroot.key`).  So only ZFSBootMenu's
prompt has to go, and nothing in the pool changes: no property, no dataset.
Adopt:

- checks `keys/disk-passphrase` against every encryption root that asks
  (`zfs load-key -n -L file://...`), and the running boot environment's key
  file against it: the same bytes, or one newline at the end of either (as
  libzfs reads a passphrase file, and as bko-holt's restore checks it).
  Either failing stops adopt with nothing changed;
- writes two hooks in `EFI/ZBM/backshot1/` on the ESP.  The `load-key.d`
  hook is `bs1-unlock-key --zfs` with the record's values at its top
  (ZFSBootMenu's own initramfs has no `/etc/backshot1`).  It waits up to 30 s
  for the drive, mounts it read-only (`norecovery` for xfs), runs
  `zfs load-key -L file://.../keys/disk-passphrase ROOT` and unmounts.  It
  never fails: without the drive, or with a key that doesn't fit,
  ZFSBootMenu asks as before.  The `boot-sel.d` hook carries what happened
  over the kexec: `bs1.zbm=ROOT:drive` (or `:typed`) on the booted kernel's
  command line, written into ZFSBootMenu's own per-boot-environment command
  line cache just before ZFSBootMenu reads it;
- adds `zbm.hookroot=UUID=ESP//EFI/ZBM/backshot1 zbm.wait_for=UUID=ESP` to
  `VMLINUZ.EFI`'s `.cmdline` section with objcopy, the way ZFSBootMenu's
  `zbm-kcl` does it (byte for byte the same image, checked on the v3.1.0
  release, but for objcopy's timestamp).  It installs binutils if objcopy
  is missing.  The original is saved in `/etc/backshot1/orig/`.
  `VMLINUZ-BACKUP.EFI` is never touched: it stays the attended way in.
  `zbm.wait_for` names only the ESP, never the drive: for a device that
  isn't there, ZFSBootMenu waits until a key is pressed.

The ESP comes back file by file under the same FAT volume ID (`mkfs.fat
-i`), so the restored image finds its hooks, and the restored boot
environment's initramfs is remade with the restored key file; nothing needs
adopting again.  It stays family A (nothing changes) when a boot
environment's encryption root has `keylocation=prompt` (its initramfs would
ask again), or the running boot environment isn't under it; when an
encryption root with no boot environment has `keylocation=prompt`; and when
the image is signed, has quoted arguments, or has a `zbm.hookroot=` or
`zbm.wait_for=` of its own; one that was family Z is put back first.  A
`VMLINUZ.EFI` replaced since adopt (a ZFSBootMenu update, which drops the
two arguments) is the original from the next adopt on, and an ESP whose
volume ID changed gets its pair rewritten.  `--luks-off` puts `VMLINUZ.EFI`
back, removes the hooks and their directories, and remakes nothing; it
changes nothing unless the recorded ESP is mounted where adopt found it,
and keeps `/etc/backshot1` when a file can't be put back.  ZFS native
encryption without ZFSBootMenu (the initramfs's own ZFS code asks) is
attended, as before, and adopt writes no record for it.

**P, booster** (PikaOS 4 on LUKS, `pika-bootstrap.sh --luks`; booster
0.12.1, Pika's fork).  booster's init opens the volume that
`rd.luks.name=UUID=NAME` names as soon as the disk appears; its
`rd.luks.key=UUID=PATH` is a file in the image, read once then (the
`PATH:UUID=` form, a key on another device, isn't built), and when it is
missing, empty or doesn't fit, booster asks.  It reads no crypttab, runs no
hook before the root is mounted, and its `/init` is its own binary.  The
backup drive comes seconds after the internal disk, but a FIFO waits for
it: booster's read blocks until a writer closes it.  Adopt:

- checks that `refind_linux.conf`'s first line (rEFInd's default) names the
  volume by `rd.luks.name=UUID=NAME` (as it is open now) or `rd.luks.uuid=`,
  that no line has an `rdinit=` of its own (one with an `rd.luks.key=` of
  its own is `own`), and that `/usr/bin/busybox` is there with the applets
  the preinit and the helper need (`--list`: `ln mknod sh cat mount umount
  mkdir rmdir rm mkfifo sleep grep findfs timeout`); anything else is
  family A, as is booster without `refind_linux.conf`;
- writes `/usr/local/lib/backshot1/booster-preinit` (busybox `sh`: it links
  the applets `bs1-unlock-key` needs under `/backshot1-bin`, makes
  `/dev/null` (the kernel's `/dev` has only `console` before booster mounts
  devtmpfs, and busybox `sh` starts no background job without it), runs
  `bs1-unlock-key --booster-fifos`, starts `bs1-unlock-key --booster-run` in
  the background, removes the FIFOs if that hasn't said it started within
  3 s, and execs `/init`) and `/usr/local/lib/backshot1/unlock-key`,
  and adds to `/etc/booster.yaml` (the original in `orig/`) `extra_files`'
  busybox, both scripts and the record, and `modules_force_load`'s
  `usb_storage`, `uas`, `sd_mod`, `xhci_pci` and the drive's file system
  (those the kernel has as modules: booster has no modprobe in the image);
- remakes each kernel's image as the restore does (`booster build --force
  --kernel-version K /boot/booster.img-K`) and checks every one has the
  preinit, busybox, the script and the record, and loads those modules;
- only then writes `refind_linux.conf`: on each line naming the volume,
  `rdinit=` and `rd.luks.key=` (and `booster.log=warning` where `quiet` would
  hide booster's warnings) before the closing ` ---`.  An image without the
  preinit wouldn't boot from such a line, so `--luks-off` puts
  `refind_linux.conf` and `booster.yaml` back before it remakes the images;
  if the check fails, everything is put back.

At boot the helper waits for booster's `/run` (booster moves it into the
booted system, with the mark), then up to 30 s for `LABEL=mybig512`
(busybox `findfs`: booster makes no by-label links), mounts it read-only
(`norecovery` for xfs) and writes the key into the FIFO.  Booster's read
of a FIFO ends only when its last writer closes it, so the helper never
leaves one unwritten: without the drive, or when booster hasn't read the
key within 600 s, it opens the FIFO read-write (which never waits), removes
it and closes it, so a waiting booster reads nothing and a late one finds
no key file, and both ask; a deadline 60 s past the drive wait does the
same should the drive's mount hang, and whatever the helper leaves when it
ends goes too.  Evidence: the mark `NAME drive`, unless this boot's kernel
log has booster's "password in keyfile ... was unable to unseal" or
"reading password:".  PikaOS's `booster.yaml` sets no
`mount_timeout`, and this booster then waits for the root for ever (its
images' `booster.init.yaml` has no `mounttimeout`), so a typed boot without
the drive has no time limit after the 30 s wait.  `.bak` and
`.dpkg-bak` copies of older images lack the preinit; adopt warns of them.
The restore puts back `refind_linux.conf`, `booster.yaml` and the scripts
with the tree and runs `booster build` per kernel in its chroot, so the
restored images carry the preinit too.

**G, Guix System** (docs/design/guix.md, "guix15-luks"): /boot and the
store are inside the LUKS1 volume, so Guix's GRUB asks first and the initrd
asks again.  Adopt:

- writes `/etc/backshot1/luks.scm`, a procedure `system.scm` applies to the
  configuration while the file exists: its boot loader inherits
  `grub-efi-bootloader`, runs Guix's own installer and then
  `/etc/backshot1/grub-image`, which writes `EFI/Guix/grubx64.efi` again
  with the same GRUB's `grub-mkimage`: a memdisk script that searches for
  the drive by label, `cryptomount -k (DRIVE)/backshot1/keys/disk-passphrase
  -u UUID`, else `cryptomount -u UUID` (the prompt), records which in
  `EFI/Guix/backshot1.env` (`save_env`: what `bs1-verify --unlock` reads),
  sets the prefix to the one Guix's image had (`(cryptouuid/HEX)/@/boot/grub`:
  an image with a memdisk says `(memdisk)/boot/grub` whatever `-p` says)
  and reads Guix's `grub.cfg`;
- puts the drive's key in `/etc/backshot1/luks-key.cpio` (mode 600), which
  `extra-initrd` names, and gives each volume `#:key-file
  "/backshot1-luks.key"`, so the initrd opens it with no prompt once GRUB
  has (by the drive or typed);
- reconfigures with root's own `guix` (the one `guix pull` updates, before
  the system profile's: bs1-common), and checks the image, every
  `grub.cfg` initrd line, the cpio and the running system's initrd.

Without the drive the passphrase is typed once, at GRUB.  The restore
brings the image back with the ESP and the cpio with /, and the new LUKS1
header (made with `--key-file cryptroot=disk-passphrase`) opens with the
same bytes, so nothing needs adopting again.  `system.scm` and the LUKS
step load `/etc/config.scm` with `primitive-load`, never `load`, which
takes a compiled copy from Guile's cache that another Guix may have made
("record ABI mismatch").  A volume the initrd doesn't open, a boot loader
other than `grub-efi-bootloader`, an `extra-initrd` of the configuration's
own, or / and the store on different file systems are family A.

**GRUB with /boot inside LUKS** (`GRUB_ENABLE_CRYPTODISK=y`, no separate
unencrypted /boot: Manjaro's installer puts /boot on the LUKS1 root,
2026-11-20).  GRUB itself asks for that volume's passphrase before Linux
starts, and nothing adopt writes reaches GRUB; the key file the installer
builds into the initramfs (`/crypto_keyfile.bin`, opening the root and swap
a second time) only spares a second prompt, so those volumes look `own` to
crypttab and the initramfs.  When the file system holding
`/boot/grub/grub.cfg` (or `/boot/grub2/grub.cfg`) is on an open LUKS
volume, and this boot's `LoaderInfo` EFI variable, if any, names GRUB (a
`grub.cfg` left behind by an install that boots by systemd-boot doesn't
count), adopt records family A, "GRUB asks for the passphrase of NAME
before Linux: /boot is inside LUKS", and changes nothing; the preflight
and phase 1 say `UNLOCK=attended` with that reason from the running system
whatever the record says (one written before this said `own`), so `cycle`
refuses the run and it goes in the attended block (below).  A separate
unencrypted /boot is as before.

**Testbed only,** like the fixed host key: with the drive and the laptop
together, anyone can read the disk.  Never on a release image, nor a machine
outside the testbed.  `backshot1 adopt --luks-off` (`bs1-adopt --luks-off`)
puts the files back and remakes the initramfs (B: `moss boot sync`);
`BS1_LUKS=no` skips the step.

The preflight and phase 1 check the drive's key against every volume, and
say how the system unlocks, `UNLOCK=`:

- `drive`: the drive's key opened every typed volume at this boot (the
  journal's key-device mount for S, B and NixOS's systemd stage 1,
  `/run/backshot1-unlock` for D, E, S's Ubuntu keystore and NixOS's
  scripted stage 1, and
  `bs1.zbm=ROOT:drive` on `/proc/cmdline` for Z, whose volumes are the ZFS
  encryption roots that ask; `backshot1 unlock` shows it);
- `own`: every volume opens by itself;
- `attended`: someone types at boot (family A, GRUB reading /boot from
  LUKS, a system adopted before this, or one not rebooted since adopt);
- `none`: nothing encrypted.

Phase 1 writes it to the baseline's `unlock.txt`, and `verify` then checks
that each volume the drive opened before the backup was opened by the drive
at the restored system's boot too, not typed.  Boots without the drive wait
30 s before the prompt.

## Attended runs go first, in one block

A run whose `UNLOCK=` is `attended` (family A, or a library backup made
before unlocking from the drive existed, which has no `unlock.txt`) needs the
person once: at the restored system's first boot.  So does an ostree run's
rollback visit.  The backup is of the running, unlocked system, and the
reboot into the stick asks for nothing.  So when a session has several runs
to do:

- Do every attended run first, one after another, while the person is there;
  the unattended runs follow.
- End the block on a system that needs nobody: restore an unattended backup
  from the library (or start the first unattended run) before the person
  leaves.  A restored system left at its unlock prompt stops every later run,
  since the next one starts from it.
- When an attended restored system hasn't answered SSH a minute or two after
  its reboot, send the person a push notification to type the passphrase, so
  nobody has to watch the screen through the restore.  An `UNLOCK=drive`
  system reboots like any other; if it isn't back after 5 min, it is at its
  prompt (the drive's key failed): push then.
- To bring an old library backup of a LUKS install into the unattended
  order: restore it (typed, once more), `backshot1 adopt`, reboot, and take a
  new backup.

The same order holds for BackShot2's runs on the Thinkpad (its tier 3).

## What guards the wipe

- `bs1-restore` wipes only with `--erase-serial` naming this machine's disk.
- Phase 1's `PHASE1_OK` must exist and match `layout.json`.
- The whole backup is read again from the rescue system first.
- The restore plan must not mention the backup drive or the stick.
- No disks may be present besides those three, and nothing on the internal disk
  may be mounted.

## What's expected to differ after the restore

`bs1-manifest`'s `EXPECTED` list: what a boot changes (`/var`, caches, histories),
the time the backup took, and what `bko-holt` remakes on purpose (the initramfs,
`grub.cfg` and the ESP's loader).  Anything else fails `verify`.

It compares each file's extended attributes as well as its content, owner, mode
and mtime: SELinux labels, capabilities, POSIX ACLs and `user.*` attributes.
A few files a boot writes again must keep their content (`REMADE`):
`/etc/machine-id`, systemd-boot's `entry-token`, and OpenRC's saved console
keymap and encoding (`/usr/libexec/rc/console/*`, Gentoo's `save-keymaps`).
Some of the ESP is held to its bytes although the rest of it may change
(`KEPT`): on AnduinOS (a baseline with `EFI/AnduinOS/shimx64.efi`) shim,
MokManager and the GRUB signed with the machine's own MOK, in
`EFI/AnduinOS` and `EFI/BOOT`, which the restore keeps and never
reinstalls; on Solus (a baseline with clr-boot-manager's shim or a Solus
kernel) the whole ESP at `/boot`.  A snapshot that only one side has is expected, snapper's or
AnduinOS's Disk Snapshots Manager's (its scheduler runs 2 min after every
boot), and with the manager's, its records' changes too; phase 1 and
`verify` hold both schedulers for their boot.

The manifest has every regular file's inode flags (`FS_IOC_GETFLAGS`), but
compares them only under `ostree/`: elsewhere they are the filesystem's as
much as the file's.  They decide one thing there.  `mkfs.btrfs --rootdir
--compress` (systemd-repart, so openSUSE Aeon's installer) leaves its files
with the compress flag (`lsattr`'s `c`) and no `btrfs.compression` property,
a state no mounted btrfs can make: the kernel writes the property, with the
mount's algorithm, whenever `FS_IOC_SETFLAGS` sets the flag, and removing
the property clears the flag.  So a regular file that had the flag and comes
back with the flag and a `btrfs.compression` of `zstd`, `zlib` or `lzo`, and
no other attribute changed, is expected (12,115 files on Aeon,
aeon-20261121-20261123-131915), and the comparison says how many there
were.  A file that gained the property without having had the flag is not,
nor is any against a baseline from before the flags were recorded.

Where GRUB's core image names /boot/grub's LV by UUID,
`(lvmid/VG-UUID/LV-UUID)` (Gentoo with / on LVM, as on the Thinkpad),
phase 1 records each EFI binary's embedded ids in the baseline's
`lvmid.txt`, and `verify` checks that every one is now the restored LV's
`lvs -o vg_uuid,lv_uuid` (the restore's `lvmid-after.txt`), as HoltTest1's
ht1.sh does.
That's a check on the whole trip, `bko-holt`'s boot stage and the live backup
included; SticTest1 is what tests `rustic-bko` itself, inode flags too.  A
baseline taken before this has no attributes, and only the rest is compared.

## Rehearsal

`bs1-qemu.py TEST_IMG GUEST_DIR TOOLS_DIR WORK_DIR` runs these same scripts on a
test guest, as three boots: source, rescue, restored.  `--bios` is for a guest
that doesn't boot by UEFI, `--virtio` for one whose initramfs has no NVMe driver.

**Where to run one.**  On any amd64 farm host that caches the guest, not
just Chopin and Cinqcent, where the rehearsals below ran by habit.  Place it
with `testvm/pick-host`.  Its `--guest` checks that the guest is in the
host's cache (`vms/GUEST/*.qcow2`; a small host caches only the published
guests, so a guest still unpublished is found only on its builder), and its
`--reserve` checks the work dir and memory against the host's ceiling:

    H=$(testvm/pick-host --reserve D 6 --guest GUEST)        # from ~/git/backotter

D is the work dir's peak: the guest's overlay, the USB drive with the
repository, the restored internal disk and the stick's copy (kinoite44
peaked at 10.3 GiB, so `--reserve 12 6`; alpine324-uefi at 1.9 GiB, so `--reserve 4 6`).
M is 6 because the VM gets 6 GiB.  A rehearsal under a small host's ceiling
(9 GiB of RAM disk, 10.1 GiB memory) may add `--class small` to leave the
big pair free.  On H, run it from /mnt/bko-ram under `cmp-run.sh --reserve D
6` with a `WEAR_TAG=bs1:GUEST` and a `--port` of its own:

  - TOOLS_DIR: a directory of your own with `bko-holt` and `rustic-bko`
    side by side (bs1-qemu.py copies `TOOLS_DIR/bko-holt` and
    `TOOLS_DIR/rustic-bko`), copied or linked from the host's
    `holt-bin/linux/bko-holt` and `bko-bin/linux/rustic-bko`, which every
    host holds as the same set.  `~/backotter-work` itself is not one.
  - TEST_IMG: `images/linux/test/*.img` on a host that has one.  The small
    hosts have no `images/` yet (until the image fan-out, img-speed I8), so
    copy one from Chopin or Cinqcent (`scp` host to host) and compare their
    SHA-256 before the run.

On a LUKS guest the harness's USB drive (`BKOBACKUPA`) stands in for
`mybig512`, and the guest's passphrase goes into its
`keys/disk-passphrase`.  When adopt sets up unlocking from the drive, the
source also runs adopt a second time (nothing may change), boots with the
drive and nothing typed (`UNLOCK=drive`), boots without it (the prompt must
come, and is answered), then boots with it for the backup; the restored
system boots with the drive and nothing typed, and once more without it.
`--luks-typed` answers the guest's own first boot on the serial console
(`--luks-monitor`: on the keyboard, for Omarchy's Plymouth),
`--strip-embedded-key` first takes a guest's built-in key file away so it
asks (`gentoo-systemd`, `chimera-uefi`), and `--luks-off` checks that
`bs1-adopt --luks-off` puts every file back.  A ZFS guest's `zfs-passphrase`
counts too: family Z (`void-zbm-enc`) runs the same boots with
`--luks-typed` (ZFSBootMenu asks at the source's first boot), and the files
compared include the ESP's `EFI/ZBM`.  Family G (`guix15-luks`, `--virtio --luks-typed`:
Guix's GRUB asks at the source's first boot) too: OVMF connects only the
devices QEMU's bootorder names, so on a boot that gives the internal disk
a bootindex the drive gets the last one, as a laptop's firmware connects
its USB disks; a boot by the NVRAM's entry has no bootindex and OVMF
connects everything.

Passed on 2026-11-09: Ubuntu 26.04 and Fedora 44 (UEFI), Devuan 6 (OpenRC, and
`bs1-adopt` alone on plain sysvinit), Alpine 3.24, Artix (dinit and s6), Void
(musl), AerynOS and NixOS 26.05; on 2026-11-11, Kinoite 44 (ostree, on both
hosts, with its rollback booted); on 2026-11-12, Chimera (`chimera-uefi` and
`chimera-zfs`, FreeBSD's userland, on both hosts) and Gentoo (`gentoo`, the
Thinkpad's OpenRC install in small, on both hosts, with GRUB's embedded
lvmid that of the restored vg0/root).  Artix's OpenRC and runit editions have no
guest.  On 2026-11-13, unlocking from the drive, on Cinqcent: `ubuntu26-luks`
(family S, Ubuntu's dracut; with `--luks-off`), `omarchy` (E, Limine and
Plymouth), `chimera-uefi` (D, Chimera's port of initramfs-tools) and
`gentoo-systemd` (S, `rd.luks.uuid` on the command line), the last two with
`--strip-embedded-key`, and `alpine324-luks` (`own`: left alone).  Debian's
own initramfs-tools has no LUKS guest yet.  On 2026-11-20, Ubuntu 26.10's "ZFS + encryption"
(`ubuntu2610-zfsenc`, `make-ubuntu.sh --uefi --zfs-enc`; family S, the
keystore's dracut hooks; `--luks-typed --luks-off`) on Cinqcent, run
`ubuntu-26.10-20261120-022116`: every boot with the drive opened
`keystore-rpool` with its key, the restored one too, and without it the
prompt came after the 30 s wait.  On 2026-11-14, Garuda (`garuda`,
GRUB on btrfs with snapper's 4 snapshots restored as shared ones, dracut
through `dracut-rebuild`, the source booted by the firmware's own entry for
its fallback loader; on both hosts) and BigLinux (`biglinux`, `--virtio`,
btrfs with Timeshift's snapshot left out and systemd-swap's `@swapfile`,
booted by the `BigLinux` NVRAM entry a Calamares install leaves; on both
hosts).  On 2026-11-24, Guix's encrypted layout (`guix15-luks`,
family G, `--virtio --luks-typed`) on Cinqcent, run
`guix-20261124-154640`: every boot with the drive was opened by GRUB with
the drive's key and the initrd with the cpio's, nothing typed
(`UNLOCK=drive`, the source's and the restored system's), and without the
drive GRUB asked once.  On 2026-11-27, the first on a small host: `alpine324-uefi`
(`--virtio`) on Cage, placed by `pick-host --reserve 4 6 --guest
alpine324-uefi --class small`, run `alpine-3.24.4-20261127-012714`, 25
checks ok.

Every rehearsal also runs the preflight, and checks that the snapshot is named
`/mnt/bs1-src` and keeps a file's real access time.  On an ostree guest
(`kinoite44`) the restored rollback then boots, pointed at by a `default` key
in grubenv (bootupd's `grub.cfg` ignores `grub2-reboot`'s `next_entry`), and
`bs1-verify --boot` checks it, then the default again, with grubenv put back
as it was.  The internal disk is the guest's own size.
