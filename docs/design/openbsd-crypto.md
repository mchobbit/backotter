# OpenBSD softraid CRYPTO support (approved)

2026-11-14. The user's request: "Add GEFS support to OpenBSD rescue disk". Approved the same day with the recommended decisions.
Settled with the user the same day: "GEFS" means OpenBSD's own full-disk
encryption, softraid's CRYPTO discipline (`bioctl -c C`), the OpenBSD
counterpart of FreeBSD's GELI. GuestTest1's audit already listed it as open
("OpenBSD UEFI / softraid CRYPTO: open"); UEFI stays out of this spec.

**Conventions.** est. marks an estimate. *Check* marks a fact to settle on the
first guest before the code that depends on it. OpenBSD facts are from
bioctl(8), softraid(4), boot(8), installboot(8) and autoinstall(8) as of 8.0.

## Summary

- bko-holt dumps and loads an OpenBSD system whose root disklabel sits inside
  a softraid CRYPTO volume: it records the volume and its chunk, recreates
  the volume on the target with a passphrase, rebuilds the volume's
  disklabel and file systems inside it, restores, and makes it bootable.
- The rescue image needs no new code: GENERIC.MP has softraid with CRYPTO,
  and `bioctl` and `installboot` are already in the image and its `tools`
  check. One new check line only (below). It goes into the image rebuild the
  "rescue images" session already has planned (bko-holt 0.2.6 + rel7), or
  the one after it: no rebuild of its own.
- One new guest, `openbsd8-crypt`, joins HoltTest1BSD; SticTest1 treats it as
  `openbsd8` (the encryption sits below FFS, as GELI does below the pool).
- ~2.5 d (est.).

## What a softraid CRYPTO system looks like

| | Plain `openbsd8` | `openbsd8-crypt` |
|---|---|---|
| sd0 | MBR, OpenBSD slot, disklabel: `a` FFS `/`, `b` swap | MBR, OpenBSD slot, disklabel: `a` fstype `RAID` (the whole slot) |
| softraid volume | none | `sd1` (CRYPTO, chunk `sd0a`), its own disklabel and DUID: `a` `/`, `b` swap |
| hw.disknames | `sd0:DUID` | `sd0:DUID0,sd1:DUID1` |
| fstab | names partitions by sd0's DUID | names them by sd1's DUID |
| boot | biosboot + `/boot` on sd0 | biosboot + `/boot` on sd0, written by `installboot sd1` (installboot writes to the chunks); `/boot` asks `Passphrase:` on the console, then boots `sd1a:/bsd` |

## The rescue image (`stick/openbsd/`)

- Nothing to build. GENERIC.MP has `softraid0` and the CRYPTO discipline;
  `bioctl`, `installboot`, `disklabel`, `fdisk` and `newfs` are in base and
  already checked.
- `/dev` is an mfs filled from the root's `MAKEDEV all` copy. *Check*: it has
  nodes for one more `sd` than the plain case needs (stick + target disk +
  volume = sd2), so up to sd3 for a two-disk target.
- `checks.sh` gets one line: `check softraid 'dmesg | grep -q "^softraid0 at root" && echo present'`.
  It rides in whichever image build comes next; until then the image works
  without it.
- The image's own memory (3 GiB, no swap) is enough: bioctl's KDF is
  bcrypt_pbkdf, a little CPU and no real memory. The restore OOM is
  rustic-bko's, being fixed elsewhere, and isn't changed by encryption.

## bko-holt `dump` (`holt/src/other/labelled.rs`, `bsdlabel.rs`)

- Today `disk_names` keeps every `sdN`, so a volume `sd1` would be dumped as a
  second physical disk. Instead: run `bioctl softraid0` (it lists each
  volume, its level and its chunks; `bioctl sdN` for one), and mark each
  softraid volume as a **virtual disk** under the volume's JSON, not a disk
  of its own. Record per volume:
  - `level` (`CRYPTO`), `device` (`sd1`), `chunks` (`["sd0a"]`), the size,
    the volume's disklabel text and DUID (as for a disk today), and its
    partitions, file systems and mounts exactly as a plain disk's.
  - `key`: `passphrase` or `keydisk` (bioctl shows a keydisk chunk). Never
    the passphrase or key itself.
  - `kdf_rounds`: *check* whether bioctl or the metadata shows it; if not
    readable cheaply, omitted, and load uses bioctl's default (`-r auto`).
- The chunk's disklabel partition keeps fstype `RAID`, as it is now; the
  probe doesn't try to read inside it.
- `load` refuses other softraid levels (RAID0, RAID1, RAID5, CONCAT, RAID1C)
  and keydisk CRYPTO with a clear message: they're dumped, so the report is
  complete, but not loaded (see open decisions).

## bko-holt `load` (`holt/src/other/load.rs`, `load/commands.rs`)

In `other_partition`, after the chunk's disk is partitioned and labelled
(fstype RAID, as recorded):

1. `bioctl -c C -l /dev/sdXa [-r ROUNDS] [-p FILE] softraid0`, then parse
   `softraid0: CRYPTO volume attached as sdN` for the new volume's device.
   Plan prints it as `<volume of sd1>` until it runs.
2. `disklabel -R sdN` with the volume's recorded text: the DUID comes back
   with it (as for disks today), so the restored fstab is right as it is.
3. The volume's file systems, mounts, restore and fsck as for a plain disk,
   on sdN.
4. Boot: `installboot -r TARGET sdN` (installboot finds the chunks and
   writes biosboot and `/boot` to sd0), instead of `installboot sd0`.
5. Close: unmount only; the volume stays attached (see "What O1 found": `bioctl -d` clears the bootable flag).

`--map` still names physical disks only; a volume follows its chunk. The
two-disks-one-DUID problem (`ht1-bsd.sh` line ~258) now concerns the volume's
DUID too: the rescuer must not be a copy of `openbsd8-crypt`.

## Entering the passphrase

- **At load:** with no key file, bioctl asks on the terminal twice
  (`New passphrase:`, `Re-type passphrase:`), as cryptsetup does for LUKS
  today; load's runner already hands the terminal to such commands
  (`load/exec.rs:42`). With `--key-file NAME=FILE`, NAME being the volume's
  device in the dump (`sd1`) or its DUID, load passes `-p FILE`: bioctl
  wants it owned by root and mode 0600 or tighter, so load copies it into
  its run dir with that mode first. (O4: bioctl wants mode exactly 0600, and
  a final newline, which it strips; load's copy adds one when the file
  lacks it, so LUKS- and GELI-style key files work as they are.) The passphrase is new: nothing on the
  source can be copied, so the user may pick a different one.
- **At boot:** OpenBSD's `/boot` asks `Passphrase:` on the console. The
  test guests type it with `testvm/console-boot.py`, which already does this
  for LUKS and GELI: add `passphrase:` at the start of a line to its prompt
  regex, and the guest gets `vms/openbsd8-crypt/console-passphrase` (mode
  600, made by make-openbsd.sh; a test value, never in the repo). vm-lib
  already uses it when the file is there.

## Test guest: `openbsd8-crypt`

- `testvm/make-openbsd.sh 8.0 vms/openbsd8-crypt --crypto` (est. 0.5 d): the
  same autoinstall, plus the installer's root-disk encryption answers in
  `install.conf` (*check* the exact question text in 8.0's installer:
  "Encrypt the root disk with a (p)assphrase or (k)eyfile?" = `p`, then the
  new passphrase twice). Fallback if autoinstall can't: `drive-install.py`
  drops to the installer's shell first and runs `bioctl -c C -l sd0a -p FILE
  softraid0`, then installs to sd1.
- Same disklabel template inside the volume (`/` 4G+, 2 GB swap). BIOS+MBR,
  serial console, host name obsd8-crypt.
- Stored and published like the other guests (vm-store); four-host farm as
  usual.

## Checks

- **HoltTest1BSD:** `openbsd8-crypt` joins the group (`ht1-bsd.sh`'s
  `openbsd*` case, with the key file passed to `load` and console-boot.py
  typing at the restored system's first boot). Passes when the restored
  system boots through the passphrase, sd1's DUID and every file match, and
  `bioctl sd1` shows CRYPTO on sd0a. Also unit tests: dump parses `bioctl`'s
  output (captured from the guest), load's plan for a CRYPTO layout, and the
  refusals.
- **ImgTest2's OpenBSD panel:** same guest, from the rescue image. Blocked
  today by rustic-bko's restore memory (session "Bound rustic-bko restore
  memory; unblock OpenBSD T5.3"), and it needs an image with this
  bko-holt, so it runs after the next image build that carries it. restic-bko
  half can run before the rustic fix lands.
- **SticTest1:** an entry for `openbsd8-crypt` with `openbsd8`'s check (the
  archivers see the same FFS); recorded as ≡ openbsd8 in design-classes, so
  Test2 runs one of the two per round.
- Not in scope: BackShot1 (the Thinkpad doesn't run OpenBSD).

## Steps and estimate

| # | Step | Est. |
|---|---|---|
| O1 | The guest: `make-openbsd.sh --crypto`, console-boot.py prompt, settle the *checks* (installer text, bioctl output, KDF rounds, /dev nodes) | 0.5 d |
| O2 | dump: softraid volumes as virtual disks, unit tests from O1's captured output | 0.5 d |
| O3 | load: bioctl create, volume label, installboot on the volume, close, key file, refusals; unit tests | 0.75 d |
| O4 | HoltTest1BSD with `openbsd8-crypt` (both archivers), fix round | 0.5 d |
| O5 | `checks.sh` line; SticTest1 entry; design-classes; holt README | 0.25 d |
| O6 | ImgTest2 OpenBSD panel, after the next image build and the restore-memory fix | (waits) |

~2.5 d (est.) for O1-O5. O2-O3 are source code: High-effort subagents.
bko-holt's version bump goes with whatever bump comes next, as agreed with
the "rescue images" session, so no extra image build.

## Open decisions

1. **Other softraid levels.** Recommended: CRYPTO only; RAID1, CONCAT and
   RAID1C (encrypted mirror) are dumped and refused at load. RAID1 would add
   ~0.5 d and a second guest.
2. **Keydisk CRYPTO.** Recommended: refuse. It needs a second disk holding
   the key at boot, which the target won't have unless the user maps one.
3. **KDF rounds.** Recommended: keep the source's if dump can read them,
   otherwise bioctl's default.
4. **UEFI/GPT OpenBSD.** Recommended: separate work (it's the other half of
   the audit's open item, and the image already boots UEFI).

## What O1 found (2026-11-14)

The guest `openbsd8-crypt` is on chopin (`vms/openbsd8-crypt`, snapshot
`fresh-install`); captures in `openbsd-crypto/`. It overrides the spec above
where they differ:

- **Installer:** autoinstall does it: `Encrypt the root disk with a
  (p)assphrase or (k)eydisk?` = `p`, `New passphrase?` once (autoinstall
  only). It runs `bioctl -Cforce -cC -l sd0a -s softraid0`, no `-r`.
- **bioctl** (`bioctl softraid0` = `bioctl sd1`): a volume line
  `softraid0 0 Online SIZE sd1 CRYPTO`, then one chunk line `0 Online SIZE
  0:0.0 noencl <sd0a>`. Load finds the new volume the way install.sub does:
  `bioctl softraid0`'s Device column after the create, not a kernel message.
- **KDF rounds** aren't shown by bioctl, but are in the chunk's metadata
  (`/dev/rsd0a` sector 16 on, magic `marcCRAM`, version 6; the bcrypt_pbkdf
  hint, type 3, holds rounds: 242 here). Dump reads them; load passes `-r`.
- **The volume has its own MBR** (slot A6 at 64) inside, then its disklabel
  (label "SR CRYPTO"). Load runs `fdisk -iy sdN` before `disklabel -R sdN`.
- **No `/boot` file on the root**: installboot puts the second stage in the
  chunk's softraid boot area. `installboot sd1` on the volume is still the
  call.
- **The volume is 592 sectors smaller than its chunk**; both read as
  17179566080 bytes in bioctl.
- **/dev:** `MAKEDEV all` makes sd0-sd9: enough.
- **Boot prompt:** `Passphrase:` goes to the BIOS console before boot.conf is
  read (it's inside the volume). It reaches serial only through SeaBIOS's
  serial mirroring under `-nographic` (vm_boot's way). The BIOS keyboard
  buffer holds 15 keys, so console-boot.py now types one key every 0.03 s.
- **O4 (HoltTest1BSD):** `DUID.a` names work for newfs and mount; ht1-bsd.sh
  attaches the source's volume in the rescuer with the passphrase, and the
  rescuer and restored boots go through console-boot.py. ht1-bsd.sh runs
  rustic-bko only.
- **`bioctl -d` clears the bootable flag** that installboot sets (the
  metadata's volume flags go from 0x0c, NOAUTOASSEMBLE + BIOC_SCBOOTABLE, to
  0x04; `sr_ioctl_deleteraid`). So load's close leaves the volume attached
  (as the installer does) and says so; shutting the rescue system down
  detaches it. No test may detach a restored volume before its first boot:
  ht1-bsd.sh's verify rescuer runs on `--snapshot`.
- **bioctl `-p`**: the file must be root's, mode exactly 0600, and end in a
  newline (it reads one line with fgets).
