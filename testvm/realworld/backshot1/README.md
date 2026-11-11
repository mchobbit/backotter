# BackShot1

A bare-metal backup and restore of a real laptop, driven over SSH by a Claude
session.  The person's only jobs are booting the rescue stick and clicking Done.

The machine is named in `thinkpad.conf`: the Thinkpad, on its one NVMe disk,
backing up to a USB drive labelled `mybig512`.  Everything acts on the disk with
that serial number, never on a `/dev` name.

It handles any Linux `bko-holt` supports, on any stack of partitions, LUKS, LVM,
md, btrfs and ZFS on that disk, booted by UEFI or BIOS.  It backs up every real
filesystem the running system has mounted from the disk, and stops, before
changing anything, if the disk holds a filesystem that isn't mounted.  Not
covered: the BSDs, illumos and Haiku, which need their own rescue images, and
btrfs subvolumes or ZFS datasets that aren't mounted.

An encrypted install needs its passphrase, in plain text, in
`backshot1/keys/disk-passphrase` on the backup drive, and someone to type it at
the restored system's first boot.

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
  systemd, OpenRC, sysvinit, runit, dinit or s6.  On NixOS it writes
  `/etc/nixos/backshot1.nix`, imports it from `configuration.nix` and rebuilds;
  a system built from a flake needs those settings added by hand.
- The USB Ethernet adapter stays plugged in: the images are wired-only.

## The run

    backshot1 stage TOOLS_DIR     scripts and tools onto the backup drive
    backshot1 phase1              back up; check --read-data; prints RUN=...

The person clicks Done, which authorizes erasing the internal disk.  The stick and
the backup drive stay plugged in.

    backshot1 to-stick            reboots into the stick, once, by BootNext

If that doesn't work, the laptop comes back up as it was, and the person boots the
stick by hand.  It needs a UEFI system with `efibootmgr`, so not AerynOS.

    backshot1 find-rescue         by the laptop's MAC addresses; hostname is backotter
    backshot1 plan NAME           reads the whole backup, plans; erases nothing
    backshot1 phase2 NAME         wipes, restores, compares, redoes the boot loader
    backshot1 reboot              BootNext is the restored system

Once `thinkpad.local` answers again:

    backshot1 verify NAME         the checks; exit 0 and RESULT say PASS

On an ostree system, then the rollback, as below:

    backshot1 check-boot NAME     the deployment this boot started, against the baseline

The tree is mounted at `/run/bs1-src` and backed up as `/mnt/bs1-src`
(`--as-path`), so every snapshot is `latest:/mnt/bs1-src`, older backups' too.

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

## Encrypted runs go first, in one block

A run of an install whose LUKS asks for a typed passphrase needs the person
once: at the restored system's first boot.  The backup is of the running,
unlocked system, and the reboot into the stick asks for nothing.  So when a
session has several runs to do:

- Do every such run first, one after another, while the person is there; the
  unattended runs follow.
- End the block on a system without a passphrase: restore an unencrypted
  backup from the library (or start the first unattended run) before the
  person leaves.  A restored system left at its unlock prompt stops every
  later run, since the next one starts from it.
- When the restored system hasn't answered SSH a minute or two after its
  reboot, send the person a push notification to type the passphrase, so
  nobody has to watch the screen through the restore.
- A LUKS install that unlocks without typing (a key file in its initramfs,
  or SSH into the initramfs: dropbear-initramfs, dracut-sshd,
  mkinitcpio-dropbear) needs nobody, and can run anywhere in the order.

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
That's a check on the whole trip, `bko-holt`'s boot stage and the live backup
included; SticTest1 is what tests `rustic-bko` itself, inode flags too.  A
baseline taken before this has no attributes, and only the rest is compared.

## Rehearsal

`bs1-qemu.py TEST_IMG GUEST_DIR TOOLS_DIR WORK_DIR` runs these same scripts on a
test guest, as three boots: source, rescue, restored.  `--bios` is for a guest
that doesn't boot by UEFI, `--virtio` for one whose initramfs has no NVMe driver.

Passed on 2026-11-09: Ubuntu 26.04 and Fedora 44 (UEFI), Devuan 6 (OpenRC, and
`bs1-adopt` alone on plain sysvinit), Alpine 3.24, Artix (dinit and s6), Void
(musl), AerynOS and NixOS 26.05.  Artix's OpenRC and runit editions have no guest.

Every rehearsal also runs the preflight, and checks that the snapshot is named
`/mnt/bs1-src` and keeps a file's real access time.  On an ostree guest
(`kinoite44`) the restored rollback then boots, pointed at by a `default` key
in grubenv (bootupd's `grub.cfg` ignores `grub2-reboot`'s `next_entry`), and
`bs1-verify --boot` checks it, then the default again, with grubenv put back
as it was.  The internal disk is the guest's own size.
