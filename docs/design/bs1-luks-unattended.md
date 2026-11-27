# BackShot1: LUKS installs that unlock from the backup drive (draft)

2026-11-13. Built the same day (L1-L5: R1-R4 and R6 passed; "Built" at
the end says where it differs from this design and what the rehearsals
showed; L6, the Thinkpad, waits for its next LUKS run). The user asked for it
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
`backshot1/keys/disk-passphrase` (label `mybig512`; xfs, not ext4 as this
said first: adopt takes the type from the drive). It has no final
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
| **N: NixOS** (built 2026-11-16, "Built, family N" below) | the Thinkpad's NixOS 26.05 (Calamares: LUKS2 root on xfs and LUKS2 swap, systemd stage 1, systemd-boot) | `/etc/nixos/backshot1-luks.nix`, imported by `backshot1.nix` while it exists, for each typed `boot.initrd.luks.devices` volume: systemd stage 1 gets `keyFile = "PATH:LABEL=..."` and `keyFileTimeout`; scripted stage 1 gets `preOpenCommands` that copy the key into `/crypt-ramfs`, `keyFile` and `fallbackToPassword = true`; both get the USB modules and the drive's file system; then `nixos-rebuild switch` | systemd stage 1: systemd-cryptsetup asks; scripted: luksroot's prompt | systemd stage 1: journal, as S; scripted: `/run/backshot1-unlock`, as D |
| **A: attended** | non-systemd dracut (Void, Gentoo OpenRC), Alpine's mkinitfs (`cryptkey` reads only from inside the initramfs), booster without rEFInd's `refind_linux.conf` (booster on PikaOS is family P, "Built, family P" below), ugrd, ZFS native encryption, ostree (load refuses LUKS there) | nothing | the prompt | none; the run is attended |

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
  so load formats with those bytes, and that keyslot stays. A crypttab key
  file in the restored tree (Manjaro's `/crypto_keyfile.bin`, which its
  initramfs carries) still gets its own keyslot in the boot stage, beside
  it, unless it opens the volume already (commands.rs, near
  `"the restored key file"`; until 2026-11-20 load skipped it, and a
  restored Manjaro asked for the passphrase at every boot). The
  `path:LABEL=` field is never read as a file: it adds no keyslot.
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
   no NixOS LUKS guest exists. *Settled 2026-11-16: N built for the
   Thinkpad's LUKS NixOS ("Built, family N").*
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

## Built (2026-11-13)

Where the code differs from the design above:

- **Files.** `bs1-luks` (adopt's step, sourced by `bs1-adopt` after
  `bs1-common`) and `bs1-unlock-key` (the keyscript and the `bs1key` hook's
  helper), both staged by `backshot1 stage` and `bs1-qemu.py`. The evidence
  (`unlock_how`, `unlock_mode`, `luks_key_test`) is in `bs1-common`.
- **The record** lists each volume as `NAME:LUKS-UUID:HOW`, HOW being S, D
  or E, so a late volume on a D or E system (opened by the booted system
  under systemd) takes the S form and S's evidence.
- **Own** also covers an early volume with `cryptkey` on the command line
  or `/crypto_keyfile.bin` in mkinitcpio's FILES (Alpine's mkinitfs, a
  busybox `encrypt` install with its key built in).
- **Any typed volume adopt can't handle** makes the whole install family A,
  with every reason in the record; nothing else changes.
- **S on the command line:** `rd.luks.options=`, `rd.luks.key=` or
  `rd.luks.crypttab=` there would override crypttab, so such an install is
  family A rather than half set up (but on mkinitcpio's unified kernel
  images, from 2026-11-18: "Built, family S on unified kernel images"
  below). The device field becomes `UUID=` only
  when the command line names the volume's UUID (`rd.luks.uuid`).
- **E:** the hook tests the key with `cryptsetup open --test-passphrase`
  before leaving `/crypto_keyfile.bin`; a wrong key (exit 2) leaves no file,
  so `encrypt` asks at once. The drop-in is `zz-backshot1.conf`, after
  Omarchy's `omarchy_hooks.conf`, which sets HOOKS too.
- **No `*.bs1-old` initramfs.** Adopt checks the new image instead and puts
  everything back (and remakes the initramfs) if the remake or the check
  fails; the reboot preflight wants is the other guard.
- **Driver:** `backshot1 adopt --luks-off` and `backshot1 unlock`. Phase 1
  prints `UNLOCK=` before `RUN=`.

**Rehearsals** (Cinqcent, bko-holt 0.2.5 from /bkoscratch/backshot2/bin,
the 20261112 test image, `bs1-qemu.py --discard-on-pass` under
`cmp-run.sh --reserve 12 6`). Each did steps (a) to (f) above, and all
passed:
- R1 `ubuntu26-luks`, `--luks-typed --luks-off`: family S. Ubuntu 26.04's
  dracut is host-only with systemd, so crypttab's line goes in by itself; the
  drive-unlocked boot showed the key-device mount in the journal, and
  `--luks-off` put every file back as it was.
- R2 `omarchy`, `--virtio --luks-typed --luks-monitor`: family E. The
  `bs1key` hook ran before `encrypt` from the `zz-backshot1.conf` drop-in,
  its `--test-passphrase` worked in the initramfs (`drive`, not `fetched`),
  /run reached the booted system, and Limine's remade `limine.conf` was as
  the manifest expects. Without the drive Plymouth asked after the wait.
- R3 `chimera-uefi`, `--virtio --strip-embedded-key`: family D. Chimera's
  cryptsetup-scripts port runs the keyscript; without the drive it waited
  30 s and then asked "Please unlock disk cryptroot:".
- R4 `gentoo-systemd`, `--virtio --strip-embedded-key`: family S with
  `rd.luks.uuid` on the command line; the crypttab line (device made
  `UUID=`) gave the key, and host-only dracut took it in.
- R6 `alpine324-luks`, `--virtio`: `own` (its `cryptkey` key file in the
  initramfs); adopt changed nothing but the record, the preflight said
  `UNLOCK=own`. Its first try failed verify's swap check, which compared
  `/dev/dm-N` names, and the restored system numbered its LVs differently;
  `swap_names` now names a device-mapper swap by its `/dev/mapper` name
  (baselines with `dm-N` names are compared as before), and the rerun passed.
- R5 waits for a `debian13-luks` guest (Q3). Until then Debian's own
  cryptroot (the Thinkpad's Debian 12) is covered by R3's port of it and
  by the Thinkpad's next Debian 12 run (L6), where a failure costs one
  typed boot.

## Built, family N (2026-11-16)

The Thinkpad's NixOS 26.05 is a Calamares LUKS install, so N was built for
it, unrehearsed (no NixOS LUKS guest). Confirmed in its nixpkgs
(26.05.13627.c56a87d8e41c, `nixos/modules/...`), read on the Thinkpad:

- **systemd stage 1 is the default** (`system/boot/systemd/initrd.nix:195-196`),
  and the Thinkpad has it. Its crypttab is luksroot's `stage1Crypttab`
  (`system/boot/luksroot.nix:590-611`, put in by `:1221`), one line
  `NAME DEVICE KEYFILE OPTIONS` a volume: `keyFile` goes in verbatim
  (`:608`), so `PATH:LABEL=mybig512` reaches systemd-cryptsetup-generator,
  which luksroot puts in the initrd (`:1232`); `keyFileTimeout` becomes
  `keyfile-timeout=Ns` (`:605`). This boot's journal on the Thinkpad has the
  initrd's own lines ("Running in initrd.", "Starting Cryptography Setup for
  luks-..."), so family S's journal evidence works here as it is.
- **What each stage 1 refuses,** which is why the module sets things under
  `lib.mkIf config.boot.initrd.systemd.enable` and its negation: with
  systemd, `fallbackToPassword` must stay false ("implied", `:1097-1099`)
  and `preOpenCommands`/`postOpenCommands` empty (`:1114-1117`); without
  it, `keyFileTimeout` must stay null (`:1091-1093`).
  `reusePassphrases` set by hand fails under systemd (`:1108-1111`); the
  Thinkpad's is the default (`:679-681`), and the module doesn't touch it.
- **Scripted stage 1:** `preOpenCommands` runs after luksroot's wait for
  the volume and before it opens it (`:544`), inside the batch that mounts
  the `/crypt-ramfs` ramfs first and unmounts it after (`:125-126`,
  `:149-150`), so the key copy lives there and is gone before stage 2.
  stage 1 has busybox, util-linux's `mount` and `blkid`
  (`system/boot/stage-1.nix:114-120`), udev's 60-persistent-storage rules
  for `/dev/disk/by-label` (`:257`), the modprobe helper
  (`system/boot/stage-1-init.sh:261`), udev settled before
  `preLVMCommands` (`:281-286`), and `cryptsetup` (luksroot `:1160`). The
  key file is waited for 10 s (`:262`, the default at `:39`), and a missing
  or failing key falls back to the prompt with `fallbackToPassword`
  (`:267-281`): a boot without the drive asks after WAIT + 10 s there.
  `/run` is moved into the root (`stage-1-init.sh:674`), so the
  `/run/backshot1-unlock` marker reaches the booted system, as D's does.
- **Modules:** `boot.initrd.availableKernelModules` `usb_storage uas sd_mod
  xhci_pci`, and `boot.initrd.supportedFilesystems = [ FSTYPE ]` (a list is
  accepted, `tasks/filesystems.nix:385`, `attrNamesToTrue`; for xfs,
  `tasks/filesystems/xfs.nix` adds `xfs crc32c` to the initrd).

Where the code differs from the table, or adds to it:

- **Volumes** come from the evaluated configuration, as nixos-rebuild
  evaluates it (`import <nixpkgs/nixos> { }`, so `<nixos-config>`), not from
  the `.nix` files. A LUKS volume on the internal disk that isn't open, or
  isn't in `boot.initrd.luks.devices` (stage 1 doesn't open it), makes the
  install family A. One with a `keyFile` not ours, FIDO2, a YubiKey, a GPG
  card, clevis, or `tpm2-device=`/`fido2-device=`/`pkcs11-uri=` in
  `crypttabExtraOpts` is `own`.
- **The record:** `family=N generator=nixos`, each volume `NAME:UUID:N`.
  `unlock_how` N reads `/run/backshot1-unlock` when it names the volume
  (scripted), else the journal as S (systemd), so a later change of stage 1
  needs no new record. bko-holt's dump has no `boot.initramfs.generator` on
  NixOS (null in the library run `nixos-26.05-20261116-200906`), so phase 1's
  generator comparison stays S, D and E only.
- **The import:** `adopt_nixos`'s `backshot1.nix` has
  `imports = builtins.filter builtins.pathExists [ ./backshot1-luks.nix ];`,
  so the module is in while the file exists and `--luks-off` is the generic
  one: remove the file (listed in `made`) and the record, rebuild. The
  record goes even when an earlier one (the Thinkpad's family A) was there
  first, since that one is in `made` too; with no record, `UNLOCK=` is
  `attended`, as family A's would be.
  Evaluated on the Thinkpad without the file, the system's initrd is the one
  it runs now.
- **Order:** `adopt_nixos` rebuilds first (as before), then the LUKS step
  writes the record and the module and rebuilds again, so a first adopt
  rebuilds twice; a run that changes nothing, with the running system's
  initrd the one the configuration makes, rebuilds nothing.
- **The check after the rebuild:** the running system's initrd is the
  configuration's `system.build.initialRamdisk`, systemd-boot's
  `EFI/nixos/HASH-initrd-linux-VER-initrd.efi` is on the ESP, and that
  initrd's crypttab (systemd) or `stage-1-init.sh` (scripted) gives each
  volume the drive's key. Otherwise everything is put back and rebuilt.
- **Restore:** on NixOS by UEFI bko-holt's restore runs nothing in the
  chroot (220c013); the restored system boots the backed-up ESP's initrd,
  which has all of this, and LUKS UUIDs are kept, so the `by-uuid` devices
  still hold.
- **The drive is written at boot under systemd stage 1:** its key-device
  mount is `ro`, and XFS still replays a dirty log on a read-only mount of a
  writable device (crypttab can't add `norecovery`), as ext4 does under S.
  Scripted stage 1 mounts with `norecovery` (xfs) or `noload` (ext2-4).

Checked on the Thinkpad, read-only: the module as the code renders it for
its two volumes parses, and with the real configuration (copied to /tmp,
`NIXOS_CONFIG`) both stage-1 kinds evaluate to a system (assertions pass):
systemd's crypttab gets `KEY keyfile-timeout=30s` on both lines, scripted's
`preLVMCommands` passes `sh -n`; `luks_adopt` ran with every write sent to
/tmp and the rebuild stubbed (N, the record, the module; the check's
failure path put everything back).

On the Thinkpad itself (2026-11-16/17, systemd stage 1, systemd 260.5): a
real adopt (two rebuilds, the check passed), then a boot with the drive and
nothing typed (`UNLOCK=drive`), then a hands-free cycle round
(nixos-26.05-20261116-210139: restore, the restored system's boot opened
both volumes with the drive's key, verify PASS, 0 unexpected).  A boot with
the drive unplugged: `dev-disk-by\x2dlabel-mybig512.device` timed out at
31 s, both keydev mounts failed by dependency, systemd-cryptsetup logged
"key file ... missing" and the console asked once; the typed passphrase
opened both volumes, and `backshot1 unlock` said both `typed`,
`UNLOCK=attended`.  Not yet: a wrong key, and scripted stage 1 at boot at
all.

## Built, family B (2026-11-18)

AerynOS's initramfs comes prebuilt with its kernel (dracut's, systemd
inside: systemd-cryptsetup and its generator, xfs.ko; USB storage and UAS
built into the kernel), so it reads no crypttab of the installed system's,
and adopt recorded the Thinkpad's AerynOS 2026.11 as family A.  Its boot
entries are blsforme's: `moss boot sync` writes them to the ESP with every
`/usr/lib/kernel/cmdline.d` and `/etc/kernel/cmdline.d` `*.cmdline` added,
and an `rd.luks.uuid=` for the root's volume of its own.  A by-hand trial
on the Thinkpad (systemd 262.2, LUKS2 root, xfs `mybig512`) put
`rd.luks.key=UUID=/backshot1/keys/disk-passphrase:LABEL=mybig512` and
`rd.luks.options=UUID=discard,keyfile-timeout=30s` in a cmdline.d file, and
the next boot unlocked from the drive, nothing typed.

- **Family B** (`bs1-luks`): prebuilt initramfs, `moss` there, systemd as
  init.  A volume is early when it's under `/`, `/usr` or swap, or the
  running command line names it in `rd.luks.uuid=` or `rd.luks.name=`.
  Each early one gets `rd.luks.uuid=`, `rd.luks.key=` and one
  `rd.luks.options=` in `/etc/kernel/cmdline.d/90-backshot1-luks.cmdline`
  (no comment line: nothing says blsforme drops one; the name and
  `/etc/backshot1/made` say whose it is).  The generator takes the last
  `rd.luks.options=` (and `rd.luks.key=`) for a UUID, and a volume's own
  over the one for every volume, so the options are the ones the other
  cmdline.d files already put in force (`lk_bls_eff`), each once, with
  `keyfile-timeout=30s` in place of any other.  Another file's key for the
  volume (or a token in its options) is `own`.  Other cmdline.d files are
  never edited; adopt warns of one with an `rd.luks.key=` (a hand-written
  trial), which `--luks-off` would leave.  A late volume is S's crypttab
  line.
- **Remake and check:** `moss boot sync`, then every `loader/entries/*.conf`
  in `/efi`, `/boot` or `/boot/efi` (else the internal disk's ESP or
  XBOOTLDR, mounted read-only) must name each volume and give it the
  drive's key and those options as the last of each; if not, everything
  is put back.  A rerun whose files are unchanged syncs again only if the
  entries lack it.  `--luks-off` removes the file and syncs.
- **Evidence:** as S, the journal's key-device mount
  (`/run/systemd/cryptsetup/keydev-luks-UUID`).
- **The key file** is made mode 600 by adopt and by `backshot1 stage`:
  systemd-cryptsetup warned of the trial's 0644 (it unlocked anyway).
- **The restore** remakes nothing on AerynOS: the backed-up ESP's entries
  carry the line, and the file comes back with `/etc`.

No rehearsal: there is no AerynOS guest with LUKS.  Unit tests
(`test_bs1_luks.py`: BlsCmdline, AdoptB, Evidence) run `luks_adopt` and
`luks_off` against a fake `moss` that writes an entry as blsforme does.

## Built, family S on unified kernel images (2026-11-18)

The Thinkpad's Arch (mkinitcpio 42 with `systemd` + `sd-encrypt`, presets
`default_uki`/`fallback_uki` built with ukify, systemd-boot finding them in
`EFI/Linux`) had `rd.luks.name=U=root rd.luks.options=U=discard
root=/dev/mapper/root rootflags=... rw quiet` in `/etc/kernel/cmdline`,
baked into each image's `.cmdline`.  `rd.luks.options=` overrides
crypttab's options (`keyfile-timeout` too), so adopt said A.  By hand:
`root UUID=U none discard` in `/etc/crypttab.initramfs`, the two
`rd.luks` arguments out of `/etc/kernel/cmdline`, `mkinitcpio -P`, a typed
boot; then adopt chose S and a hands-free cycle passed with
`UNLOCK=drive` on both boots.  `bs1-luks` now does that itself.

- **What mkinitcpio bakes** (`build_uki`, read in mkinitcpio 42.1's
  source): with no `--cmdline`, the first of `/etc/kernel/cmdline` and
  `/usr/lib/kernel/cmdline`, then every `*.conf` under `/etc/cmdline.d`
  (`find -xtype f`, `sort -V`), `#` lines left out, newlines made spaces;
  with none of them, `/proc/cmdline`.  A preset's `NAME_cmdline` or
  `ALL_cmdline` becomes `--cmdline FILE`, which skips the rest.  ukify is
  handed `--cmdline=@FILE` and `--config=/etc/kernel/uki.conf`; the
  command line wins over the config's `Cmdline=`, so `uki.conf` never
  gives these images their command line (only with `--no-cmdline`, which
  adopt refuses).
- **Decision from the files, not /proc/cmdline.**  On S with mkinitcpio and
  a preset with `NAME_uki`, the next boot's command line is what those
  files give (`lk_uki_text`), plus whatever `/proc/cmdline` has that
  they don't (`lk_uki_stray`: the boot loader's).  An argument adopt took
  out of a file counts as the file's until the reboot (its copy in
  `orig/`), so a rerun before the reboot changes nothing, and after it
  `/proc/cmdline` has none.
- **The move** (`lk_uki_map`), only with sd-encrypt's crypttab.initramfs
  and `StubInfo` (systemd-stub's EFI variable, so this boot did come
  through a unified image): each `rd.luks.name=U=NAME` (NAME must be the
  open mapping's name), `rd.luks.uuid=[luks-]U` and
  `rd.luks.options=U=OPTS` (or `luks.`) of a typed volume opened early
  gives its crypttab.initramfs line, `NAME UUID=U KEY OPTS,keyfile-timeout=30s`
  (lk_tab_edit's sixth field: the command line's options were the ones in
  force, so they replace the line's; `keyfile-offset`, `-size` and `-erase`
  go, as they'd misread the drive's key).  `rd.luks.options=OPTS` for every
  volume applies, as in systemd-cryptsetup-generator, only to one crypttab
  doesn't list.  Refused (A, nothing changes): TPM2/FIDO2/PKCS#11 options,
  an `rd.luks.key=` that isn't the drive's, `rd.luks.crypttab=`,
  `rd.luks.data=`, `luks=`, an unknown UUID, a name that isn't the open
  one, a crypttab.initramfs line for another volume (once the command line
  names nothing, the initramfs opens every line), an argument in
  `/usr/lib/kernel/cmdline` or a link, a preset's own command line, no
  file at all (`/proc/cmdline` baked), the boot loader adding one.
- **The rewrite** (`lk_uki_drop`): only the `rd.luks`/`luks.` words go, each
  with the whitespace before it (a line's first word with the one after),
  `#` lines and everything else byte for byte, no final newline added.
  The original goes to `orig/` once, as every file adopt changes, so
  `--luks-off` restores it exactly and remakes the images.
- **Order:** crypttab.initramfs is written before the command-line file,
  and one `mkinitcpio -P` bakes both, so no image ever has a command line
  that names nothing without the crypttab line (a remake in between, say a
  kernel update, would find crypttab's key under the old options: typed,
  no hang).  A failed write, remake or check puts everything back.
- **Check** (`lk_check`, `lk_uki_check`): each image remade under
  `EFI/Linux` has crypttab's line in its initramfs (as before, by
  `lsinitcpio -x`, which reads unified images) and a `.cmdline`
  (`lk_pe_section`: the PE section table read with `od`, since Arch has
  no python by default and objcopy is binutils') with no
  `rd.luks.options`, `.key` or `.crypttab`; after a move, no `rd.luks`
  argument at all.  At least one unified image must have been remade.  The
  `.cmdline` check runs on any S system's unified images, a warning only
  where adopt didn't move anything and can't read the section.
  `lk_pe_section` gave objcopy's bytes for ZFSBootMenu's release EFI
  (`.cmdline`, `.osrel`, `.sdmagic`) and Arch's `linuxx64.efi.stub`
  (`.sdmagic`), on Cinqcent.
- **Left family A, and why:** arguments from a systemd-boot type 1 entry's
  `options`, GRUB's `GRUB_CMDLINE_LINUX`, Limine's or rEFInd's config.
  The remake adopt runs (and the restore's boot stage) is the initramfs
  generator's; it doesn't write those, so the command line and the crypttab
  couldn't change in the same step, and the boot loader is something
  BackShot1 never edits (Limine's and kernel-install's hooks write their
  configs again from other sources).  So is a dracut or kernel-install
  unified image: their remake doesn't rebuild it from
  `/etc/kernel/cmdline`.
- **Evidence:** unchanged, S's key-device mount (`keydev-NAME`, NAME the
  kept mapping name) in the journal of the first boot after adopt.

No rehearsal: no farm guest is an Arch install with sd-encrypt and unified
images (`arch` has no LUKS; `omarchy` is busybox `encrypt` with Limine).
Unit tests (`test_bs1_luks.py`: UkiCmdline, AdoptUki) run `luks_adopt` and
`luks_off` on a fake root, with a fake `mkinitcpio -P` that builds PE images
the way build_uki takes the command line, and a fake `lsinitcpio`.

## Built, S's Ubuntu ZFS keystore (2026-11-20)

Ubuntu 26.10's "ZFS + encryption" (the Thinkpad, run
ubuntu-26.10-20261119-061810, accepted attended): `rpool` natively
encrypted with a raw key, `keylocation=file:///run/keystore/rpool/system.key`,
that file on ext4 in LUKS2 `keystore-rpool` on the zvol `rpool/keystore`;
swap plain dm-crypt with a random key; dracut 111 with systemd.  Since
bf5eb2f adopt saw the keystore and recorded family A.

- **What opens it** (read from the source, not guessed): zfs-linux
  2.4.4-1ubuntu4's `debian/patches/ubuntu/4001-dracut-Open-and-mount-luks-keystore.patch`
  (LP #2070066) adds to the dracut `zfs` module's pre-mount hook
  `90-zfs-load-key.sh`, for an encryption root `E` keyed from
  `file:///run/keystore/E/...`: `systemd-cryptsetup attach "keystore-E"
  /dev/zvol/E/keystore` (no key file, no options), then a mount at
  `/run/keystore/E`; the module now depends on `systemd-cryptsetup`.  No
  crypttab line or `rd.luks` argument is involved, so nothing S writes
  reaches it.  systemd's `cryptsetup.c` (v258, `verb_attach`,
  `discover_key`): with no key file it first tries `VOLUME.key` in
  `/etc/cryptsetup-keys.d` and `/run/cryptsetup-keys.d`, and when that key
  fails (`-EAGAIN`) drops it and asks with the password agent, as before.
- **The hook:** family S, the volume's HOW `K`.  A dracut module of
  BackShot1's, `/usr/lib/dracut/modules.d/99backshot1` (`check` 255,
  `depends` zfs; `add_dracutmodules+=" backshot1 "` in
  `90-backshot1.conf` beside the USB modules and the drive's file system)
  installs the record, `bs1-unlock-key`, `cryptsetup`, and two pre-mount
  hooks: `85-backshot1-keystore.sh` runs `unlock-key --keystore`, which
  waits up to 30 s for the drive and copies its key to
  `/run/cryptsetup-keys.d/keystore-rpool.key`; `95-backshot1-keystore-done.sh`
  runs `--keystore-done`, which marks `keystore-rpool drive` in
  `/run/backshot1-unlock` when the mapping is open and that key opens the
  zvol (`cryptsetup open --test-passphrase`; exit 2 is `typed`, anything
  else `fetched`), and removes the key.  Without the drive it marks `typed`
  and systemd-cryptsetup asks.  crypttab, the pool's properties and the
  boot loader are untouched.
- **Detection** (`lk_keystore_ok`): open as `keystore-POOL` on the zvol
  `POOL/keystore`; family S on dracut; `POOL` its own encryption root with
  `keylocation=file:///run/keystore/POOL/...`, and `/` under it; a dracut
  `zfs-load-key.sh` with `systemd-cryptsetup attach` and `/run/keystore/`.
  Anything else stays A, with the reason (initramfs-tools' Ubuntu up to
  25.04 among them).
- **Check:** each remade image lists `85-backshot1-keystore.sh`,
  `90-zfs-load-key.sh` and `95-backshot1-keystore-done.sh` under
  `hooks/pre-mount`, `systemd-cryptsetup`, the script and the record, and
  has the systemd module; else everything is put back.
- **Evidence:** `unlock_how` K reads `/run/backshot1-unlock`, as D and E.
- **The restore:** load makes the keystore with the drive's key
  (`--key-file keystore-rpool=...`, unchanged), and the boot stage's dracut
  takes the module from the restored `/usr/lib/dracut/modules.d`.

Unit tests: `test_bs1_luks.py` AdoptZfsKeystore (adopt, rerun, `--luks-off`,
an image without the hooks, seven refusals), UnlockKeyKeystore, Evidence.
Guest: `make-ubuntu.sh --uefi --zfs-enc` (`ubuntu/storage-zfs-enc.yaml`,
Subiquity's guided ZFS with a password: the same layout as the Thinkpad's).

**Rehearsal** (2026-11-20, Cinqcent, `cmp-run.sh --reserve 14 6`, work in
`/mnt/bko-ram`): `ubuntu2610-zfsenc` (26.10 server, Subiquity's guided ZFS
with a password, then a full upgrade: zfs-linux 2.4.4-1ubuntu4, dracut 112,
systemd 261), bko-holt 0.2.6 from 3fc3d5d, the 20261120 test image,
`bs1-qemu.py --luks-typed --luks-off`: PASS, 38 checks, BackShot1 run
`ubuntu-26.10-20261120-022116`.  Adopt took it as `family S ...
keystore-rpool:UUID:K`, both images checked; a second adopt changed
nothing; `--luks-off` put every file back.  The boot with the drive logged
`dracut-pre-mount: BackShot1: the backup drive's key is
systemd-cryptsetup's for keystore-rpool` and no prompt (`UNLOCK=drive`);
without it, the wait and then systemd-cryptsetup's "Please enter
passphrase for disk keystore-rpool" (`typed`).  The preflight said
`UNLOCK=drive`; the restored system booted from NVRAM unlocked by the
drive, `bs1-verify` 0 unexpected, and without the drive asked again.

## Built, family S on openSUSE's sdbootutil (2026-11-21)

openSUSE Tumbleweed 20261119 by Agama (the Thinkpad): systemd-boot 262.2
through shim, managed by sdbootutil 1+git20261111
(`/etc/sysconfig/bootloader`: `LOADER_TYPE="systemd-boot"`), dracut 112
with systemd, LUKS2 then LVM `system` (btrfs root, swap), the root
snapper's `@/.snapshots/1/snapshot`, SELinux enforcing.  Adopt classified
it S correctly, but its check failed and rolled back, twice: the image it
read was the ESP's, and that one hadn't been remade.

- **What boots** (read from `/usr/bin/sdbootutil`, `install_kernel`):
  each kernel's BLS entry on the ESP names `/ENTRY-TOKEN/KVER/linux-SHA1`
  and `initrd-SHA1`, the files' checksums, one entry per snapper snapshot
  (`rootflags=subvol=@/.snapshots/N/snapshot`).  The initrd is dracut's
  output (host-only, so `/etc/crypttab` is in it), copied there by
  sdbootutil; dracut's own `/boot/initrd-KVER` boots nothing.  A new
  snapshot's entry (snapper's `10-sdbootutil.snapper` plugin, zypper's
  pre/post pairs) reuses its parent's initrd.
- **The remake:** `lk_sdbootutil` (sdbootutil on PATH and `LOADER_TYPE`
  systemd-boot or grub2-bls) makes dracut's arm of `lk_remake` run
  `sdbootutil mkinitrd`: dracut anew (`--no-reuse-initrd`) for each kernel
  of the running snapshot, the file copied under its new checksum, that
  snapshot's entries rewritten, and `bootctl cleanup` drops any initrd no
  entry names.  `--luks-off` and the roll-back after a failed check are the
  same command after the put back.  The record keeps `generator=dracut`,
  what the dump says.  sdbootutil on a transactional system (/ read-only
  in fstab: MicroOS, Aeon) is attended.
- **Snapshots:** only the running snapshot's entries get the new initrd.
  Another snapshot's entry boots that snapshot, whose own `/etc/crypttab`
  lacks the drive's key, and keeps the initrd it had (made from such an
  `/etc`), so booting an older snapshot asks for the passphrase, as it
  should; adopt says how many such entries there are.  Snapshots made
  after adopt inherit the new initrd through sdbootutil's reuse.
- **Check:** the initrds the running root's entries name (`lk_sdbu_entries`:
  `bootctl --print-boot-path`, `btrfs subvolume show /`'s path against each
  entry's `rootflags=subvol=`), not "written since the stamp" (a
  reproducible initrd keeps its name and file, and the ESP's FAT
  timestamps read as an hour in the future here, which is how the failed
  run found the stale image at all); at least one such entry, each with an
  initrd and no `rd.luks.options|key|crypttab` in its options, and each
  initrd's crypttab giving the volume the drive's key, as for any S image.
- **The restore** (`bko-holt load` plan on this layout, 0.2.6): the dump
  says systemd-boot `via_shim`, so the boot stage keeps the restored ESP
  ("only the boot entries are made", no `bootctl install`); luksFormat
  keeps the LUKS UUID, the filesystems keep theirs, the restore stage
  makes snapshots 2-4 again; so every restored entry and its initrd are
  the backed-up ones and boot as they did.  Its dracut loop in the chroot
  remakes only `/boot/initrd-KVER`, which boots nothing: harmless for
  BackShot1 (same machine), but a restore onto other hardware would boot
  the source's host-only initrd.  Running `sdbootutil mkinitrd` in the
  boot stage's chroot would close that; not built (untested in a rescue
  chroot: it reads `/proc/cmdline` as a last resort, `bootctl`'s view of
  the ESP and the root subvolume, and may chroot into a snapshot itself).

Unit tests: `test_bs1_luks.py` AdoptSdbootutil (detection; the running
snapshot's entries; adopt, rerun, `--luks-off` with a fake sdbootutil that
names initrds by checksum and cleans up; a remade initrd without the line,
no entry for the running snapshot and an entry whose options override
crypttab each put everything back; transactional is attended).

**On the Thinkpad** (2026-11-21, the fixed `bs1-luks` bind-mounted over the
staged one in a private mount namespace, so `here` stayed on mybig512):
adopt wrote crypttab, the drop-in and the record, `sdbootutil mkinitrd`
made `initrd-aa80211...` for entry `-1` (the default), whose crypttab is
`cr_nvme-eui.002538f751b6167e-part2 /dev/disk/by-uuid/774040c6-...
/backshot1/keys/disk-passphrase:LABEL=mybig512 x-initrd.attach,keyfile-timeout=30s`;
entries 2-4 kept `initrd-4dca1b8...`.  `--luks-off` put crypttab back and
gave entry 1 a fresh initrd with the original line (`bootctl cleanup`
removed aa80211); a second adopt made `initrd-aa80211...` again (dracut's
`--reproducible`), and a third changed nothing.  The drive-unlocked boot
and BackShot1 itself are next.

## Built, family P (2026-11-27)

PikaOS 4 on LUKS (`testvm/realworld/pika-bootstrap.sh --luks`: the GTK
installer's Btrfs-on-LUKS2 layout, `crypt_root`, rEFInd with its NVRAM
entry) boots booster 0.12.1 (Pika's fork of anatol's; its source and the
26.08.20-4 ISO's binaries read for this), whose command line is rEFInd's
`/boot/refind_linux.conf`.  What booster's init does with LUKS
(`init/luks.go`, `cmdline.go`, `main.go`): it opens each volume
`rd.luks.name=UUID=NAME` or `rd.luks.uuid=` names as soon as the device
appears; `rd.luks.key=UUID=PATH` is a key file inside the image, read once
at that moment, and missing, empty or wrong it asks (Plymouth's prompt with
`splash`).  Its `PATH:UUID=` form ("keyfile lives on another device") logs
"I don't know how to do that"; it reads no crypttab; `enable_hooks`'
`hooks-early` run after the root is mounted; and the image's `/init` is its
Go binary (`--init-binary` is a command-line flag the restore's `booster
build` doesn't pass).  So nothing in booster itself can wait for the drive.

- **Mechanism:** a FIFO as the key file.  The kernel runs BackShot1's
  preinit first (`rdinit=/usr/local/lib/backshot1/booster-preinit`, busybox
  `sh`).  It links the applets it needs under `/backshot1-bin` (the image has
  only `/usr/bin/busybox`, no applet links and no `/bin/sh`), runs
  `bs1-unlock-key --booster-fifos` (`mkfifo /backshot1-key.UUID` for each of
  the record's P volumes), starts `bs1-unlock-key --booster-run` in the
  background and execs `/init`.  booster's read of the FIFO blocks (Go's
  `os.ReadFile`) until the helper, once booster has mounted `/run` and up to
  `wait=` s later, writes the drive's key into it and closes it; without the
  drive it ends it empty (booster logs the failed key file and asks at
  once).  The helper finds the drive by busybox `findfs LABEL=` (booster makes
  no by-label links), mounts it `ro,norecovery` (xfs), and marks `NAME drive`
  or `NAME typed` in `/run/backshot1-unlock`, which booster moves into the
  booted system.  booster wipes its root (FIFOs, key copy, links) before it
  switches root.
- **No FIFO left unwritten** (review of 1a0bcce): a FIFO no one writes
  holds booster for ever, with no prompt.  So:
  - the preinit makes `/dev/null` first (`mknod -m 666 /dev/null c 1 3`;
    booster's devtmpfs covers it later): before booster's `/init` the
    kernel's `/dev` has only `console`, and busybox `sh` can start no
    background job without `/dev/null` (shown in a user-namespace chroot
    under Debian's busybox, where `mknod` is refused: "can't open
    '/dev/null'");
  - `--booster-run` touches `/backshot1-key-started` first; the preinit
    waits up to 3 s for it and otherwise removes the FIFOs, so booster
    finds no key file and asks (the same chroot run: "no fifo", no hang);
  - `release` ends a FIFO without waiting: a read-write open (a FIFO's
    never blocks on Linux) wakes a booster waiting to open it, the FIFO is
    removed, and the close is booster's end of file; a booster not there
    yet finds no file.  The helper releases on every path without the key
    handed over: no drive, no key on it, `give` failing or timing out
    (`GIVE_WAIT` 600 s, booster never opened it);
  - `--booster-run`'s deadline, `wait=` + 60 s, releases every FIFO should
    the helper not yet have the drive's key (a mount that hangs), and
    whatever is left once the helper ends (killed, died) goes too.  The
    helper and the deadline each `mkdir /backshot1-key-claim`; only the one
    that made it hands keys or releases, so a late key can't be marked
    `drive` after the deadline released its FIFO;
  - adopt requires busybox `--list` to have `ln mknod sh cat mount umount
    mkdir rmdir rm mkfifo sleep grep findfs timeout` (`modprobe` and
    `blkid` fetch does without), else family A.
- **adopt** (`bs1-luks`, family P, generator `booster`, found after
  mkinitcpio as holt's `initramfs_json` does): the preinit and
  `unlock-key` (listed in `made`), `/etc/booster.yaml` (in `orig/`) with
  `extra_files` += busybox, the preinit, the script and the record, and
  `modules_force_load` += `usb_storage uas sd_mod xhci_pci FSTYPE`, those the
  running kernel has as modules (`modinfo -F filename` not `(builtin)`:
  booster refuses a module it has no `.ko` for, and its image has no
  modprobe); then `booster build --force --kernel-version K
  /boot/booster.img-K` per kernel (load's arm), each image checked with
  `booster ls` and `booster cat ... etc/booster.init.yaml`; then
  `refind_linux.conf` (in `orig/`): on each line naming the volume,
  `rdinit=`, `rd.luks.key=UUID=/backshot1-key.UUID` and, on a `quiet` line
  without `booster.log`, `booster.log=warning` (booster's `quiet` logs only
  errors; the failed-key line is a warning), before the closing ` ---`.
  Last, because a line with `rdinit=` and an image without the preinit
  can't boot (the kernel falls back to mounting `root=` itself); `--luks-off`
  puts both files back before remaking the images.  A first line (rEFInd's
  default) that doesn't name the volume, an `rdinit=` of the line's own, a
  name other than the open mapping's, booster without
  `refind_linux.conf`, or no busybox or one without those applets: family A.  An `rd.luks.key=` of its
  own: `own`.  A volume the booted system opens is S's, under systemd.
- **Evidence** (`unlock_how P`): the helper's mark, unless this boot's
  kernel log (`journalctl -k -b`, else `dmesg`) has booster's
  `password in keyfile ... was unable to unseal` or `reading password:`.
- **The restore:** bko-holt load restores `refind_linux.conf` (its UUID
  check reads `rd.luks.name=`; `rd.luks.key=` carries the same UUID, which
  BackShot1's restore keeps), `booster.yaml` and the scripts, and runs
  `booster build` per kernel in the chroot (host-only from the rescue's
  `/sys`, plus `modules_force_load`), so the restored images carry the
  preinit.  No bko-holt change.
- **Limits:** a `mount_timeout` in `booster.yaml` would run from booster's
  start, the 30 s wait included; PikaOS sets none, and this booster's
  default is then to wait for the root for ever (the 26.08.20-4 live
  image's `booster.init.yaml` has no `mounttimeout`; the manual's "3
  minutes" is upstream's).  A restore that changed the LUKS UUID would need holt's `refind_linux.conf`
  rewrite to carry `rd.luks.key=` too (not BackShot1's case).  Older
  `booster.img-*.bak` / `.dpkg-bak` copies lack the preinit (adopt warns).

Unit tests: `testvm/test2/tests/test_bs1_booster.py` (booster.yaml and
refind_linux.conf edits, `lk_p_volume`'s cases, adopt, rerun and
`--luks-off` on a fake PikaOS with a fake `booster`, an image without the
preinit putting everything back, a busybox without an applet, the
helper's FIFOs with and without the drive, `release` waking a waiting
reader, a key booster never reads, `--booster-run` with and without the
drive and with a mount that hangs past the deadline, the evidence, install.sh's key-file lines).  Rehearsed in QEMU
2026-11-27: `pika-bootstrap-qemu.sh --luks` PASS on Cinqcent (the install,
a typed boot, adopt, `UNLOCK=drive` with a fake XFS `mybig512` and nothing
typed, typed without it) and `bs1-qemu.py --virtio --luks-typed --luks-off`
on `pikaos4-luks` PASS on Chopin (run `pika-4-20261127-064355`, the
restored system unlocked by the drive, verify 0 unexpected)
(docs/design/pikaos.md, "Rehearsed").  Not yet run on the Thinkpad.
