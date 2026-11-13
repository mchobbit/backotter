# BackShot1: LUKS installs that unlock from the backup drive (draft)

2026-11-13. Design only: nothing is built yet. The user asked for it
("I would love if we made the LUKS runs unattended"): BackShot1 runs on
LUKS installs should need no typed passphrase. Then BackShot1All needs no
attended LUKS block, and runs aren't split by LUKS any more. est. marks an
estimate. R1-R6 are the rehearsals below, Q1-Q5 the open questions.

## Goal

When the backup drive is plugged in, an install adopted by BackShot1, and the
system BackShot1 restores from its backup, unlocks its LUKS volumes at boot
with nobody at the keyboard. Without the drive, it asks for the passphrase
as it always did, after a short wait.

## How

The passphrase is already on the drive in plain text, at
`backshot1/keys/disk-passphrase` (label `mybig512`, ext4). It has no final
newline, so its bytes are the typed passphrase. `bs1-restore` already passes
it to `bko-holt load --key-file` for every LUKS volume (`layout_keys` in
bs1-common), so the restored volumes take the same bytes.

`bs1-adopt` gains one step, once per install. It sets the installed
system's initramfs to read that file from the drive and unlock with it. If
the drive doesn't appear within `WAIT` seconds (30), or the key fails, the
normal prompt follows. This is configuration in the installed system, so it
goes into the backup. The restore's boot stage then remakes the initramfs
from it (`initramfs()` in holt/src/load/commands.rs, run in the chroot), so
the restored system unlocks itself as well.

- **Drive and path.** These come from what adopt already has:
  - the label is `BACKUP_LABEL` from bs1.conf (`mybig512` on the Thinkpad,
    `BKOBACKUPA` in bs1-qemu.py);
  - the path is adopt's own directory relative to the drive's mount point
    (`findmnt -no TARGET --target`), plus `/keys/disk-passphrase`, so
    `/backshot1/keys/disk-passphrase`;
  - the filesystem type comes from `blkid` (ext4).
- **Which volumes.** Every crypttab entry, and the `cryptdevice=` volume
  under the encrypt hook, that meets all three conditions:
  - it is LUKS on the internal disk;
  - its key field is `none`, `-` or empty, so it is typed today;
  - the initramfs opens it: `/`, the resume swap, or anything under them.

  A typed volume that only the booted system opens, such as a separate
  `/home`, uses the systemd key form below if the system runs systemd.
  Otherwise the run is attended. A volume that already unlocks with no one
  typing is left alone and recorded as `own`. That covers a key file in the
  initramfs (the `chimera-uefi`, `gentoo-systemd` and `alpine324-luks`
  guests), TPM2 and FIDO2.
- **Adopt checks the key first.** For each volume it runs
  `cryptsetup open --test-passphrase --key-file KEY DEV`, and if that fails
  it stops and names the volume. That catches a stray newline or the wrong
  passphrase before anything is changed, and before any wipe.
- **The record.** Adopt writes `/etc/backshot1/luks-unlock` with `family=`,
  `label=`, `path=`, `fstype=`, `wait=` and `volumes=NAME:LUKS-UUID ...`. It
  is in the backup, and preflight and verify read it.
- **Testbed only,** like the fixed host key. Whoever holds the drive and the
  laptop together can read the disk. It must never go on a release image or
  a machine outside the testbed. `bs1-adopt --luks-off` puts back the
  originals it saved in `/etc/backshot1/orig/` and remakes the initramfs.
  `BS1_LUKS=no` skips the step.

## Per initramfs family

Adopt finds the generator in the same order as holt's `initramfs_json`
(holt/src/boot.rs): mkinitcpio, then dracut, then initramfs-tools. That way
it and the restore's boot stage always agree. Phase 1 then compares the
record's family with the dump's `boot.initramfs.generator`.

| Family | Who | Mechanism | Fallback | Evidence for verify |
|---|---|---|---|---|
| **S: systemd in the initramfs.** dracut with its systemd module (init is systemd), or mkinitcpio with `sd-encrypt` | Fedora, EndeavourOS, openSUSE, Gentoo systemd, Ubuntu 26.04+ (whose dracut is `ubuntu26-luks`'s generator), Arch or CachyOS if their HOOKS have `sd-encrypt` | crypttab key field `/backshot1/keys/disk-passphrase:LABEL=mybig512`, option `keyfile-timeout=30s` | systemd-cryptsetup asks for the password when the key device times out or the key fails | in this boot's journal, the key device's mount unit (`run-systemd-cryptsetup-keydev\x2dNAME.mount`) started, `systemd-cryptsetup@NAME.service` succeeded, and no fallback message (R1 pins the exact lines) |
| **D: initramfs-tools** | Debian 12 (the Thinkpad's LUKS install), Debian 13, Mint, Pop, Zorin, MX, Devuan, Chimera's port | crypttab option `keyscript=/usr/local/lib/backshot1/unlock-key`, key field left as `none` | the script itself runs askpass | `/run/backshot1-unlock` says `NAME drive` (initramfs-tools moves /run into the root) |
| **E: mkinitcpio busybox `encrypt`** | Arch, Manjaro, Omarchy, CachyOS if its HOOKS have `encrypt` | own hook `bs1key`, inserted before `encrypt`, writes `/crypto_keyfile.bin`, which `encrypt` uses when the file exists | `encrypt` prompts as usual ("Invalid keyfile. Reverting to passphrase.", or no file at all) | `/run/backshot1-unlock`, as D |
| **N: NixOS** | none yet (the Thinkpad's NixOS is btrfs with no LUKS) | `backshot1.nix`: scripted stage 1 gets `preOpenCommands` to fetch the key, `keyFile`, `fallbackToPassword = true`, ext4 and the USB modules; systemd stage 1 gets `keyFile = "PATH:LABEL=..."` and `keyFileTimeout` | as the mechanism says | journal, as S |
| **A: attended** | non-systemd dracut (Void, Gentoo OpenRC), Alpine's mkinitfs (`cryptkey` reads only from inside the initramfs), booster, ugrd, ZFS native encryption, ostree (load refuses LUKS there) | nothing | the prompt | none; the run is attended |

**S, details.**
- systemd-cryptsetup-generator(8) documents the `path:device` key form (it
  mounts the device and reads the file there). If `rd.luks.uuid=` or
  `rd.luks.name=` is on the command line (as `gentoo-systemd` has), the
  crypttab entry with the same UUID supplies the key and the options, so
  the command line stays as it is. That means no GRUB, systemd-boot or
  Limine edits.
- `keyfile-timeout=` is systemd's crypttab option: "falls back to a
  password" when the key device can't be reached.
- dracut gets a drop-in, `/etc/dracut.conf.d/90-backshot1.conf`:
  `add_drivers+=" usb_storage uas sd_mod xhci_pci "` and
  `filesystems+=" ext4 "`. Hostonly dracut copies the root's crypttab line
  into the initramfs. When hostonly is off, which R1 checks on Ubuntu, the
  drop-in adds `install_items+=" /etc/crypttab "`.
- `sd-encrypt` reads `/etc/crypttab.initramfs`. Adopt edits that file, or
  creates it from `rd.luks.name=` when it doesn't exist. The modules go in
  `/etc/mkinitcpio.conf.d/backshot1.conf`.
- Contingency: if R1 shows that Ubuntu's dracut doesn't run systemd in the
  initramfs, Ubuntu moves to family A, or to `rd.luks.key=PATH:LABEL=...`
  in `/etc/default/grub` (bko-holt's boot stage runs grub-mkconfig, so the
  setting survives the restore).

**D, details.**
- Debian's `passdev` was considered, and isn't used: it only reads from the
  device, so when the drive is missing the try fails, no prompt ever comes,
  and after `tries=` the boot drops to the initramfs shell.
- Instead the keyscript (POSIX sh, busybox) works like this:
  - On `CRYPTTAB_TRIED=0` it waits up to WAIT for
    `/dev/disk/by-label/LABEL`.
  - It mounts the drive `-r -o noload -t ext4` (noload, so an unclean
    journal isn't replayed onto the drive) and copies the key to stdout.
  - It unmounts and writes the marker.
  - In every other case it runs
    `/lib/cryptsetup/askpass "Please unlock disk $CRYPTTAB_NAME: "` and
    marks the volume `typed`.
  - A wrong key costs one of the `tries=` (3), and the next try asks.
- The hook `/etc/initramfs-tools/hooks/backshot1` copies
  `/etc/backshot1/luks-unlock` into the initramfs and runs
  `manual_add_modules ext4 usb_storage uas sd_mod xhci_pci` (MODULES=dep
  needs that). The cryptroot hook copies the keyscript itself.
- The key field stays `none`. So without `--key-file`, bko-holt load still
  sees a typed volume (see bko-holt below).

**E, details.**
- The two hook files go in `/etc/initcpio/install/bs1key` and
  `/etc/initcpio/hooks/bs1key`. The install part adds the modules above and
  the run script. The run part is the same wait, mount and copy as D.
- `/etc/mkinitcpio.conf.d/backshot1.conf` rebuilds `HOOKS` with a loop, so
  that `bs1key` comes right before `encrypt`. `/etc/mkinitcpio.conf` itself
  is untouched.
- conf.d needs mkinitcpio 36 or later. If the install's is older, adopt
  edits mkinitcpio.conf instead, keeping a copy in orig/.
- The kernel command line is untouched, so Limine's
  `limine-mkinitcpio-hook` regenerating `limine.conf` doesn't matter.
- Adopt reads the HOOKS in bash, sourcing mkinitcpio.conf and conf.d as
  mkinitcpio does, so CachyOS's choice between `encrypt` and `sd-encrypt`
  is read on the install itself.

**Every family:**
- Files adopt writes start with `# BackShot1`, and each original is saved
  once in orig/.
- A second run that changes nothing remakes nothing.
- Adopt remakes the initramfs with the commands the restore uses
  (load's `initramfs()`: `mkinitcpio -P`, dracut per kernel or
  `dracut-rebuild`, Gentoo's dracut by name, `update-initramfs -u -k all`).
- It then lists the new initramfs (`lsinitrd`, `lsinitramfs` or
  `lsinitcpio`) and requires the keyscript or hook, the crypttab line,
  ext4 and usb-storage/uas in it.
- Adopt doesn't reboot. Preflight wants evidence from a drive-unlocked boot
  (below), so the person or the controller reboots once after adopt.

## bko-holt: nothing to change for BackShot1

- `bs1-restore` passes `--key-file NAME=disk-passphrase` for each volume,
  so load formats with those bytes and skips the boot stage's
  `luksAddKey` of a crypttab key file (commands.rs, near
  `"the restored key file"`). The `path:LABEL=` field is never read as a
  file.
- The boot stage remakes the initramfs in the chroot with the source's own
  generator. Every file above is in the restored root, so the new
  initramfs has them. Hostonly dracut filters crypttab by the target's root
  device, which is ours.
- A gap, for later: without `--key-file`, a key field in `path:device` form
  would make load `luksAddKey` from `TARGET/backshot1/...:LABEL=...` and
  fail at the boot stage. Load should treat such a field as external, and
  plan a problem ("give its key with --key-file") instead. This doesn't
  affect BackShot1, and families D and E keep `none` anyway.

## Preflight, phase 1 and the driver

- **The mode.** `bs1-backup --preflight` and phase 1 say `UNLOCK=drive`,
  `own`, `attended` or `none` (no LUKS), and phase 1 writes it to
  `baseline/unlock.txt`.
  - `drive` needs `/etc/backshot1/luks-unlock` to list every LUKS volume in
    the layout, and this boot's evidence to show the drive unlocked each of
    them (verify's check below).
  - Otherwise the mode is `attended`, with the reason, such as "adopted
    but not rebooted since" or "family A".
- **Both run the key test,** `cryptsetup --test-passphrase`, on every
  layout volume.
- **The driver** (`backshot1`, and bs1-all's timeouts):
  - With `UNLOCK=drive`, a LUKS reboot is like any other: it warns at 3 min
    and fails at 15.
  - The push "type the passphrase" goes out only for `attended`, or when a
    `drive` system isn't back after 5 min (the fallback prompt, after the
    30 s wait).
  - Library runs made before this have no unlock.txt, so they are
    `attended`.

## bs1-verify

- **New check,** when `baseline/unlock.txt` says `drive`: "LUKS NAME was
  unlocked with the backup drive's key, not typed", for each volume, by the
  family's evidence in the table. It fails when the volume was typed or
  there is no evidence. Baselines without unlock.txt skip it.
- **`bs1-verify --unlock`** runs that check alone. Preflight and the
  rehearsal use it.
- **No manifest changes.** The configuration is in the backup and comes
  back unchanged, the initramfs, `grub.cfg` and `limine.conf` are already in
  EXPECTED, and the markers live in /run, which the tree doesn't include.

## Docs to change once R1-R4 pass

- **README.md.**
  - The paragraph "An encrypted install needs..." changes to: the
    passphrase file, and bs1-adopt's LUKS step, with the security
    trade-off and `--luks-off`.
  - "Encrypted runs go first" becomes "Attended runs go first". It now
    covers only `UNLOCK=attended` runs (family A, and library backups
    older than this) and Bazzite's rollback visit.
  - The "Rehearsal" section gains the new harness flags.
- **docs/design/bs1-all.md.**
  - The user block is reduced to Bazzite's rollback.
  - Debian 12's "passphrase twice" goes; Q1 covers its one remaining
    typed boot.
  - The LUKS timeouts in Tooling 7 follow the driver rule above.
  - The LUKS risk line changes to "a `drive` system at its prompt means the
    key failed".

## Rehearsals

**Harness changes (bs1-qemu.py):**
- **The stand-in drive.** The harness's USB drive is already labelled
  `BKOBACKUPA`, the `BACKUP_LABEL` in its bs1.conf, so it is the stand-in
  for mybig512. No new disk is needed. "Drive absent" means `machine()`
  without `usb_drive`.
- **The key file.** It is copied to `keys/disk-passphrase` from the first
  of the guest dir's `luks-keyfile`, `console-passphrase` and
  `luks-passphrase`, with any final newline stripped (omarchy's file has
  one).
- **`--luks-typed`.** The source's first boot runs under console-boot.py,
  which answers "Please unlock disk" and "Enter passphrase" on the serial
  console. Omarchy's prompt is Plymouth's, so for it the harness swaps
  `-monitor none` for a unix socket and runs
  `omarchy/type-luks-passphrase.py`.
- **`--strip-embedded-key`,** for the guests that unlock with a key file
  (`gentoo-systemd`, `chimera-uefi`). Before adopt it does four things:
  - sets the crypttab key field to `none`;
  - drops the key from the initramfs (dracut's `install_items` drop-in,
    cryptsetup-initramfs's `KEYFILE_PATTERN`);
  - removes the file;
  - remakes the initramfs and reboots typed.

  That gives the Thinkpad's typed shape. Each guest's key is alphanumeric,
  so it can be typed.
- **New steps, in order.** Steps (c) to (f) run with no `--luks-typed` or
  `--luks-off` flag.
  - (a) adopt runs twice, and the second run changes nothing (sha256 of
    its files, no remake);
  - (b) a reboot with the drive and nothing typed answers SSH within 5 min,
    and `bs1-verify --unlock` says `drive`;
  - (c) a boot without the drive shows the prompt on the console after
    about WAIT. Omarchy shows nothing on serial, so there: no SSH for
    60 s, then the passphrase is typed. The harness types it, and
    `--unlock` says `typed`;
  - (d) the usual backup boot with the drive, preflight `UNLOCK=drive`, then
    the rescue and the restore;
  - (e) the restored boot with the drive, nothing typed, and `bs1-verify`
    PASS including the unlock check;
  - (f) a restored boot without the drive reaches the prompt, so the
    fallback survived the restore's remade initramfs.
- **`--luks-off` once,** on R1: the originals come back, and the prompt
  returns with the drive plugged in.

| R | Guest | Family, stack | Host | Flags | Settles |
|---|---|---|---|---|---|
| R1 | `ubuntu26-luks` | S (Ubuntu's dracut), GRUB through shim, LVM on LUKS2, guided Subiquity | Cinqcent (only there while Chopin is down) | `--luks-typed` | systemd in Ubuntu's initramfs; crypttab inclusion (hostonly or `install_items`); the journal lines verify matches |
| R2 | `omarchy` | E (`encrypt`), Limine, btrfs on LUKS2, Plymouth | Cinqcent, or Copland/Cage (a store guest) | `--luks-typed` | the hook order, `/crypto_keyfile.bin` taken by `encrypt`, /run carried into the root, limine.conf remade as EXPECTED; omarchy's first bs1-qemu run ever |
| R3 | `chimera-uefi` | D (Chimera's initramfs-tools port), systemd-boot, ESP as /boot, LVM on LUKS2 | Cinqcent | `--virtio --strip-embedded-key` | the keyscript and askpass paths in that port; a dinit system |
| R4 | `gentoo-systemd` | S (Gentoo's dracut), GRUB, `rd.luks.uuid` on the command line plus crypttab | Cinqcent | `--virtio --strip-embedded-key` | the command line UUID merging with crypttab's key; hostonly dracut |
| R5 | `debian13-luks` (to build, Q3) | D (Debian's own cryptroot), GRUB, LVM on LUKS2, single disk | Cinqcent | `--luks-typed` | the Thinkpad's Debian 12 path. `debian13-stack` can't stand in: it is two disks (md RAID1), and the harness and BackShot1 take one |
| R6 | `alpine324-luks` | own (key in the initramfs) | Cinqcent | none | adopt leaves it alone; `UNLOCK=own`; verify has nothing to check |

**How they run:**
- Each one runs under `cmp-run.sh --reserve 12 6` (kinoite44's measured
  peak was 10.3 GiB; these guests are smaller, est.), with WORK_DIR
  `/mnt/bko-ram/bs1luks-GUEST`, `WEAR_TAG=bs1:GUEST:luks` and a port of its
  own, 2971-2976.
- Cinqcent's 30G RAM disk takes two at a time.
- The test image and tools are those of the last bs1-qemu runs (a
  `make-img.sh --test` image; `bko-holt` from holt-bin and `rustic-bko` from
  `/bkoscratch/bs1-tools`).
- Copland didn't answer SSH at 2026-11-13 evening, and Cage's host key
  didn't match the Mac's known_hosts at .199. Both need checking before
  they take R2.
- `sd-encrypt` has no guest. It is rehearsed only if the Thinkpad's
  CachyOS turns out to use it (Q5).

**Then the Thinkpad,** at its next LUKS run (Debian 12 or CachyOS):
1. Restore the library run. It predates this, so the passphrase is typed
   once more, and for the last time.
2. `backshot1 adopt`.
3. Reboot, unattended.
4. Phase 1 makes a new library run with `UNLOCK=drive`.
5. Phase 2 and the reboot, both unattended, then verify.

From then on, that distro's runs are unattended.

## Order of work (est. 1.5-2 days)

| # | What | est. |
|---|---|---|
| L1 | bs1-adopt's LUKS step for S, D and E, `--luks-off`, the record, the initramfs post-check; the keyscript and the bs1key hook as files beside bs1-adopt, staged by `backshot1 stage` | 4-5 h |
| L2 | `bs1-verify --unlock` and the check; preflight and phase 1's `UNLOCK=` and `baseline/unlock.txt`; the driver's push rule | 2-3 h |
| L3 | bs1-qemu.py: the key-file lookup, `--luks-typed`, `--strip-embedded-key`, steps (a)-(f) | 3 h |
| L4 | R1-R4 and R6, then R5 if Q3 is yes; fixes | 4-6 h |
| L5 | README, bs1-all.md, memory | 1 h |
| L6 | The Thinkpad, at the next LUKS run (after the run in progress there now) | one run |

Source changes are written by High-effort agents (effort rule), with one
High review over L1-L3. The review reads the keyscript and hook first,
since a mistake there makes the system unbootable without a console.

## Risks

- **The drive is the key.** With the drive and the laptop together, anyone
  can read the data. Testbed only, documented, with `--luks-off` to undo.
- **An unbootable initramfs.** A broken keyscript or hook could stop the
  boot before the prompt. Three things guard against it:
  - adopt's initramfs listing;
  - the reboot preflight wants before any backup;
  - the old initramfs, kept as `*.bs1-old` until that reboot succeeds,
    where the boot loader can name it (GRUB's menu edits). On Limine and
    systemd-boot only the console helps.

  The rehearsals come first for this reason.
- **Slow USB.** If the drive enumerates after WAIT (a hub, a cold disk), the
  boot falls back to the prompt. With `drive` mode that is a FAIL plus a
  push, not a hang. WAIT is a setting in the record (Q4).
- **Boots without the drive** wait 30 s before the prompt.
- **The drive is written at boot.** systemd's key device mount (family S) is
  read-only, but ext4 still replays an unclean journal. D and E mount with
  `noload`.
- **Distro tools rewriting the files.** A package update could replace
  `/etc/crypttab` or `crypttab.initramfs` (rare), or move mkinitcpio's
  HOOKS. Preflight's evidence check catches it before the next backup.
- **Old library runs stay attended** until each is refreshed once (Q1).
- **The record's family against the dump's generator.** If they disagree
  (an install that switched generators), phase 1 stops.
- **load without `--key-file`** (the gap above): not a BackShot1 case.

## Alternatives considered

- **Key in the initramfs,** as the guests do: unattended even without the
  drive, but the key sits on the laptop's own unencrypted /boot or ESP, so
  the encryption protects nothing.
- **TPM2 (systemd-cryptenroll):** the restore formats LUKS again, so the
  enrollment is lost, and PCR policies break on a boot-loader change.
- **SSH into the initramfs** (dropbear-initramfs, dracut-sshd,
  mkinitcpio-dropbear): a network stack and keys per family in every
  initramfs. More moving parts than reading one file.
- **`rd.luks.key=` on the command line** for S: it needs editing per boot
  loader (GRUB, `/etc/kernel/cmdline`, Limine). crypttab doesn't. It stays
  the contingency for Ubuntu (S, details) and for non-systemd dracut.

## Open questions (recommendation first)

1. **Debian 12 in BackShot1All:** its A restores a library backup made
   before this, so the passphrase is typed once more. B, made after
   re-adopting, is unattended. So one typed boot remains at the start, and
   none after it. Or skip Debian 12's A and refresh it outside
   BackShot1All. Rejected alternative: setting the config up in the
   restored system before its first boot (`bs1-restore` chrooting in). The
   restore would then differ from its backup, and verify couldn't tell
   that change from real ones.
2. **NixOS and family A:** build them when an install needs them. Until
   then adopt says "attended" and why. Or build N now, unrehearsed, since
   no NixOS LUKS guest exists.
3. **Build `debian13-luks`** (`make-debian.sh` with a preseeded `crypto`
   recipe: UEFI, single disk, LVM on LUKS2, typed), est. 1-2 h. It is the
   only rehearsal of Debian's own cryptroot, which is Debian 12's path. Or
   rely on R3 (Chimera's port) and the Thinkpad's first run, where a
   failure costs one typed boot.
4. **WAIT:** 30 s. Or 10 s (a faster fallback, but more false fallbacks on
   USB) or 60 s.
5. **sd-encrypt:** check the Thinkpad's CachyOS HOOKS (in its library
   baseline on mybig512, read-only, once the run in progress there ends).
   If they have `sd-encrypt`, rehearse an `omarchy` variant switched to
   systemd hooks first. If they have `encrypt`, R2 covers it.
