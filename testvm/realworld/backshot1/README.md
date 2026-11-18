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
  `backotter`, with sudo.  Nobody types a password anywhere.
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
  static there, as on NixOS.  On an
  install with LUKS it then sets up unlocking from the drive (below), and
  stops if the drive's key doesn't open every volume.  Reboot once after
  it, with the drive plugged in: the preflight wants a boot the drive
  unlocked.
- The USB Ethernet adapter stays plugged in: the images are wired-only.

## The run

    backshot1 stage TOOLS_DIR     scripts and tools onto the backup drive
    backshot1 phase1              back up; check --read-data; prints RUN=...

The person clicks Done, which authorizes erasing the internal disk.  The stick and
the backup drive stay plugged in.

    backshot1 to-stick            reboots into the stick, once, by BootNext,
                                  waits for it (7 min) and runs find-rescue

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
  in use).  The manifest adds each regular file's link count and inode flags
  under `ostree/`.
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
| S | systemd in the initramfs: dracut (Fedora, EndeavourOS, openSUSE, Gentoo systemd, Ubuntu 26.04), mkinitcpio's `sd-encrypt`; and any volume the booted system opens under systemd | crypttab's key field `/backshot1/keys/disk-passphrase:LABEL=mybig512`, option `keyfile-timeout=30s`; `/etc/crypttab.initramfs` for `sd-encrypt`; a dracut or mkinitcpio drop-in adds the USB modules, ext4 and the drive's own file system |
| D | initramfs-tools: Debian, Ubuntu up to 25.04, Mint, Chimera | crypttab option `keyscript=/usr/local/lib/backshot1/unlock-key` (`bs1-unlock-key`), the key field left `none`; it asks with askpass when the drive's key can't be read |
| E | mkinitcpio's busybox `encrypt`: Arch, Manjaro, Omarchy | a `bs1key` hook, put right before `encrypt` by `/etc/mkinitcpio.conf.d/zz-backshot1.conf`, writes `/crypto_keyfile.bin` only if that key opens the volume |
| N | NixOS (systemd or scripted stage 1) | `/etc/nixos/backshot1-luks.nix`, imported by `backshot1.nix` while it exists, sets each typed `boot.initrd.luks.devices` volume's `keyFile`: systemd stage 1 the same `PATH:LABEL=` form and `keyFileTimeout = 30`; scripted stage 1 a copy its `preOpenCommands` take off the drive (mounted read-only, `norecovery` for xfs), with `fallbackToPassword`; then `nixos-rebuild switch` |
| B | AerynOS: systemd in the initramfs that comes prebuilt with the kernel (it reads no crypttab of the system's), the kernel command line written by blsforme from `/etc/kernel/cmdline.d` | `/etc/kernel/cmdline.d/90-backshot1-luks.cmdline` gives each volume the initramfs opens `rd.luks.uuid=UUID rd.luks.key=UUID=/backshot1/keys/disk-passphrase:LABEL=mybig512 rd.luks.options=UUID=OPTIONS,keyfile-timeout=30s`, OPTIONS those the other `cmdline.d` files already give it (one `rd.luks.options=` for the volume, since the last wins); then `moss boot sync`, and every boot entry on the ESP must carry the key and those options as the last of each; a volume the booted system opens is S's |
| A | non-systemd dracut, Alpine's mkinitfs, ostree | nothing: the run is attended |

A volume that already opens with no one typing (a key file of its own in the
initramfs, TPM2, FIDO2; on B an `rd.luks.key=` of its own in another
`cmdline.d` file) is left alone (`own`).  The kernel command line and the
boot loader are never touched, but for B's `cmdline.d` file and the entries
`moss boot sync` writes from it; another `cmdline.d` file is never edited
(adopt warns of one with an `rd.luks.key=`: `--luks-off` leaves it).  Adopt
checks the key with `cryptsetup --test-passphrase` before changing
anything, makes the drive's key file mode 600 (systemd-cryptsetup warns of
one anyone can read; `backshot1 stage` does too), saves each file it
changes in `/etc/backshot1/orig/`, records what it did in
`/etc/backshot1/luks-unlock`, remakes the initramfs as the restore does (B:
the boot entries) and checks the new one; if that fails, it puts everything
back.  Run again, it changes nothing.

**Testbed only,** like the fixed host key: with the drive and the laptop
together, anyone can read the disk.  Never on a release image, nor a machine
outside the testbed.  `backshot1 adopt --luks-off` (`bs1-adopt --luks-off`)
puts the files back and remakes the initramfs (B: `moss boot sync`);
`BS1_LUKS=no` skips the step.

The preflight and phase 1 check the drive's key against every volume, and
say how the system unlocks, `UNLOCK=`:

- `drive`: the drive's key opened every typed volume at this boot (the
  journal's key-device mount for S, B and NixOS's systemd stage 1,
  `/run/backshot1-unlock` for D, E and NixOS's scripted stage 1;
  `backshot1 unlock` shows it);
- `own`: every volume opens by itself;
- `attended`: someone types at boot (family A, a system adopted before
  this, or one not rebooted since adopt);
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
`bs1-adopt --luks-off` puts every file back.

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
own initramfs-tools has no LUKS guest yet.  On 2026-11-14, Garuda (`garuda`,
GRUB on btrfs with snapper's 4 snapshots restored as shared ones, dracut
through `dracut-rebuild`, the source booted by the firmware's own entry for
its fallback loader; on both hosts) and BigLinux (`biglinux`, `--virtio`,
btrfs with Timeshift's snapshot left out and systemd-swap's `@swapfile`,
booted by the `BigLinux` NVRAM entry a Calamares install leaves; on both
hosts).

Every rehearsal also runs the preflight, and checks that the snapshot is named
`/mnt/bs1-src` and keeps a file's real access time.  On an ostree guest
(`kinoite44`) the restored rollback then boots, pointed at by a `default` key
in grubenv (bootupd's `grub.cfg` ignores `grub2-reboot`'s `next_entry`), and
`bs1-verify --boot` checks it, then the default again, with grubenv put back
as it was.  The internal disk is the guest's own size.
