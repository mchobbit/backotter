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

      sudo bash /PATH/TO/mybig512/backshot1/bs1-adopt

  It sets the hostname, installs the keys, and adds sshd and mDNS if missing.
- The USB Ethernet adapter stays plugged in: the images are wired-only.

## The run

    backshot1 stage TOOLS_DIR     scripts and tools onto the backup drive
    backshot1 phase1              back up; check --read-data; prints RUN=...

The person boots the stick with the backup drive still attached and clicks Done,
which authorizes erasing the internal disk.

    backshot1 find-rescue         by the laptop's MAC addresses; hostname is backotter
    backshot1 plan NAME           reads the whole backup, plans; erases nothing
    backshot1 phase2 NAME         wipes, restores, compares, redoes the boot loader
    backshot1 reboot              BootNext is the restored system

Once `thinkpad.local` answers again:

    backshot1 verify NAME         the checks; exit 0 and RESULT say PASS

## The hands-free cycle

Nobody has to be at the laptop for a restore, once the firmware is set up:

- The stick must be in one of the laptop's own USB ports, not on a hub.  On the
  X13, behind an external hub, a boot aimed at the stick reached it 1 time in 5
  and otherwise fell through to the internal disk.  In a laptop port it reached
  it 5 times in 5.
- The firmware's `BootMode` is `Diagnostics`; those 5 boots were all in that
  mode, and `Quick` hasn't been tried with the stick in a laptop port.  Over
  SSH, as root, no BIOS visit:

      echo Diagnostics > /sys/class/firmware-attributes/thinklmi/attributes/BootMode/current_value

- The stick and the backup drive stay plugged in.

`backshot1 stick-on` adds a boot entry for the stick's EFI partition, outside
the boot order.  (Putting the stick first in `BootOrder` instead failed the one
time it was tried, with the stick on the hub.)
`backshot1 to-stick` sets `BootNext` to it and reboots: the firmware boots the
stick once and clears `BootNext` itself.  Only the firmware's boot list changes,
nothing on the disks, so a backup never sees it.

    backshot1 cycle NAME COUNT --erase-serial SERIAL

does COUNT rounds of: reboot onto the stick, plan, wipe and restore, boot the
restored system, verify; it stops at the first failure.  `--erase-serial`, the
internal disk's serial number, is the authorization that clicking Done gives by
hand: this erases that disk, COUNT times.

    backshot1 stick-off

removes the entry when you're done.

Still needs a person: a restored system that doesn't come up (its next boot goes
to the internal disk again, so getting back to the stick takes F12, and a hang
takes the power button; a battery defeats a smart plug), and an encrypted
system's passphrase at first boot.

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

## Rehearsal

`bs1-qemu.py TEST_IMG GUEST_DIR TOOLS_DIR WORK_DIR` runs these same scripts on a
UEFI test guest, as three boots: source, rescue, restored.
