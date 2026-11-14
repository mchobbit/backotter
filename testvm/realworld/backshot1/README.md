# BackShot1

A bare-metal backup and restore of a real laptop, driven over SSH by a Claude
session.  The person's only jobs are booting the rescue stick and clicking Done.

The machine is named in `thinkpad.conf`: the Thinkpad, on its one NVMe disk,
backing up to a USB drive labelled `mybig512`.  Everything acts on the disk with
that serial number, never on a `/dev` name.

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
  `/etc/nixos/backshot1.nix`, imports it from `configuration.nix` and rebuilds;
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
stick by hand, then runs `find-rescue`.  It needs a UEFI system with
`efibootmgr`, so not AerynOS.

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
- The firmware's `BootMode` is `Diagnostics`; those 5 boots were all in that
  mode, and `Quick` hasn't been tried with the stick in a laptop port.  It
  can be set over SSH, as root, with no visit to the firmware's setup:

      echo Diagnostics > /sys/class/firmware-attributes/thinklmi/attributes/BootMode/current_value

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
As a safety net the cycle still counts the entries after every round and
stops once there are more than `BS1_ENTRY_GROWTH` (8) more than after round
1; then delete the copies (`efibootmgr -b XXXX -B`) before the next cycle.

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
remakes the initramfs from them, and the restored system unlocks itself too.
How depends on what opens the volume:

| Family | Who | How |
|---|---|---|
| S | systemd in the initramfs: dracut (Fedora, EndeavourOS, openSUSE, Gentoo systemd, Ubuntu 26.04), mkinitcpio's `sd-encrypt`; and any volume the booted system opens under systemd | crypttab's key field `/backshot1/keys/disk-passphrase:LABEL=mybig512`, option `keyfile-timeout=30s`; `/etc/crypttab.initramfs` for `sd-encrypt`; a dracut or mkinitcpio drop-in adds the USB and ext4 modules |
| D | initramfs-tools: Debian, Ubuntu up to 25.04, Mint, Chimera | crypttab option `keyscript=/usr/local/lib/backshot1/unlock-key` (`bs1-unlock-key`), the key field left `none`; it asks with askpass when the drive's key can't be read |
| E | mkinitcpio's busybox `encrypt`: Arch, Manjaro, Omarchy | a `bs1key` hook, put right before `encrypt` by `/etc/mkinitcpio.conf.d/zz-backshot1.conf`, writes `/crypto_keyfile.bin` only if that key opens the volume |
| A | non-systemd dracut, Alpine's mkinitfs, ostree, NixOS (for now) | nothing: the run is attended |

A volume that already opens with no one typing (a key file of its own in the
initramfs, TPM2, FIDO2) is left alone (`own`).  The kernel command line and
the boot loader are never touched.  Adopt checks the key with `cryptsetup
--test-passphrase` before changing anything, saves each file it changes in
`/etc/backshot1/orig/`, records what it did in `/etc/backshot1/luks-unlock`,
remakes the initramfs as the restore does and checks the new one; if that
fails, it puts everything back.  Run again, it changes nothing.

**Testbed only,** like the fixed host key: with the drive and the laptop
together, anyone can read the disk.  Never on a release image, nor a machine
outside the testbed.  `backshot1 adopt --luks-off` (`bs1-adopt --luks-off`)
puts the files back and remakes the initramfs; `BS1_LUKS=no` skips the step.

The preflight and phase 1 check the drive's key against every volume, and
say how the system unlocks, `UNLOCK=`:

- `drive`: the drive's key opened every typed volume at this boot (the
  journal's key-device mount for S, `/run/backshot1-unlock` for D and E;
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
own initramfs-tools has no LUKS guest yet.

Every rehearsal also runs the preflight, and checks that the snapshot is named
`/mnt/bs1-src` and keeps a file's real access time.  On an ostree guest
(`kinoite44`) the restored rollback then boots, pointed at by a `default` key
in grubenv (bootupd's `grub.cfg` ignores `grub2-reboot`'s `next_entry`), and
`bs1-verify --boot` checks it, then the default again, with grubenv put back
as it was.  The internal disk is the guest's own size.
