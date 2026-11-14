# openSUSE MicroOS and Aeon support (transactional-update)

2026-11-14. Asked for by the user: support openSUSE's transactional systems,
MicroOS and Aeon, as one of five distributions added in parallel (TrueNAS
SCALE, MicroOS/Aeon, ZFSBootMenu on Void, Ubuntu Core 24, Qubes OS), each
chosen for a restore path no current guest tests. Written the way the
immutable-system precedents were (docs/design/bazzite.md for ostree,
docs/design/vanillaos.md for ABRoot), borrowing Tumbleweed's and Leap 16's
openSUSE notes (`testvm/make-opensuse.sh`, docs/design/leap16.md).
**Status: approved 2026-11-14 by the user with every recommended decision
(the last section).**

**Conventions.** est. marks an estimate. *Check* marks a fact the sources
don't settle, to be settled on the first guest of its kind (M1, A1) before
the code that depends on it. Facts come from, read 2026-11-14:
`download.opensuse.org/tumbleweed/appliances/` (the listing: Snapshot
20261111); `sysrich/tik` (main: `usr/bin/tik`, `etc/tik/config`,
`usr/lib/tik/config`, `modules/{pre,post}/15-encrypt`, `lib/tik-core`,
README); `openSUSE/transactional-update` (`NEWS`, to 6.2.0 of 2026-10-15);
`openSUSE/health-checker` (master: `sbin/health-checker.in`,
`grub/05_health_check`); the MicroOS and Aeon pages and the openSUSE news
items on systemd-boot, FDE and XBOOTLDR. src.opensuse.org answered with a
proof-of-work page and wasn't read. Nothing has been downloaded or installed.
Copies of the sources read are in the session's scratchpad, not in the repo.
Code references are to local `main` at d5e8686.

## Summary

- **What's new for bko:** a **read-only btrfs root** that changes only
  through `transactional-update` (t-u), which writes each change into a new
  snapper snapshot and makes it the btrfs default. The next boot boots it.
  **health-checker** checks each boot and, after a failed first boot of a
  new snapshot, sets the default back to the last good one and reboots.
  Tumbleweed's snapper on a writable root (`tumbleweed`, Leap 16) is a
  different path: there the snapshots are history and the live root is
  written in place.
- **Two guests, recommended:**
  - **`microos`**: MicroOS (server), classic GRUB 2 with the
    grub2-snapper-plugin snapshot menu, UEFI with Secure Boot, no
    encryption. Built from openSUSE's `kvm-and-xen` qcow2 with a
    **combustion** config disk.
  - **`aeon`**: Aeon (GNOME), the **tik** installer's default: systemd-boot
    through shim with `sdbootutil`'s per-snapshot BLS entries, and LUKS2
    over the root sealed to a **TPM2** (swtpm) plus a recovery key. Driven
    by tik's unattended settings.
  The two cover health-checker's two code paths (GRUB's
  `health-check.state` and BLS boot counting with `systemd-bless-boot`) and
  both ways of booting an older snapshot (the GRUB snapshot menu and BLS
  entries on the ESP). Decision 1.
- **bko-holt today would mostly work, but three things break or go stale:**
  1. **The boot stage writes into a read-only snapshot.** The Fedora arm
     (`("grub", true) if self.grub_dir() == "/boot/grub2"`,
     holt/src/load/commands.rs:3091) runs `grub2-mkconfig`, and dracut may
     remake the initrd, inside the live root. On MicroOS that root is
     snapshot N, which t-u treats as immutable. The new **transactional
     arm** regenerates nothing, as Vanilla's does: the restored ESP,
     `grub.cfg`, initrds and BLS entries are byte-identical, and every UUID
     they name is kept. It checks that they still lead to kernels and
     subvolumes that exist.
  2. **health-checker's state names a btrfs subvolume ID.**
     `/var/lib/misc/health-check.state` holds `LAST_WORKING_BTRFS_ID=` and
     `LAST_WORKING_SNAPSHOT=` (a path). Its `rollback()` runs `btrfs
     subvolume set-default ${LAST_WORKING_BTRFS_ID}`. A restore makes new
     IDs whenever a subvolume was created after a snapshot
     (docs/design/btrfs-snapshots.md, "Known differences"). On MicroOS
     that always happens, since every snapshot has its own `/etc`
     subvolume made after it. So a stale ID would roll the restored machine
     back to the wrong subvolume, or to none. The boot stage rewrites the ID
     from the path.
  3. **Aeon's TPM2 token and recovery key don't survive.** `load`
     re-creates LUKS with a fresh header (`cryptsetup luksFormat --uuid`, a
     typed passphrase or `--key-file`). The `systemd-tpm2` token and the
     recovery-key slot are gone, and the sealed policy couldn't open the new
     volume key anyway. The restored Aeon boots with the passphrase and
     needs `sdbootutil enroll --method=tpm2` once. The plan says so in a
     note, and HoltTest1 does it and checks that the next boot unlocks with
     no typing. Decision 4.
- **`/etc` is a btrfs subvolume inside each snapshot**
  (`@/.snapshots/N/snapshot/etc`), not the overlay of older MicroOS. This
  dates from transactional-update 5.0.0 (2025-01). Each one is synced into
  the next snapshot by `transactional-update-sync-etc-state` at boot, from
  `/etc/etc.syncpoint`. bko-holt sees them as nested subvolumes: one family
  of snapshots per `/etc` lineage, each nested in a read-only parent. The
  restore must make each parent snapshot before its `/etc` and set `ro`
  last, and the plan stage's ordering must allow that (*check* that
  `shared.rs`'s families already do). Systems migrated from the overlay
  keep an `/etc` overlay line in fstab and `/var/lib/overlay/N/etc`; they
  are out of scope (Decision 6).
- `make-opensuse.sh` is reused: its fetch-and-verify (signed `.sha256`,
  openSUSE's key) and its SELinux relabel boot and check boot. It gains an
  image mode: no installer, the qcow2 plus a config disk. Leap 16's Agama
  work isn't needed, since neither guest uses Agama. Its mode switch in
  `make-opensuse.sh` is shared, if it has landed (Leap's L1 hadn't, at
  d5e8686).
- **HoltTest1: 1Obscure**, beside `tumbleweed` and `leap16`. Decision 5.
- ~7.2 d (est.) for both, ~4.2 d for `microos` alone.

## What MicroOS and Aeon are (as far as bko cares)

| | MicroOS (`microos`) | Aeon (`aeon`) | Nearest supported system |
|---|---|---|---|
| Release | rolling, Tumbleweed's packages; images per snapshot (now `Snapshot20261111`). Only the current one is served, so it is kept in rebuild-inputs | rolling; `Aeon-Installer.x86_64.raw.xz` (2.2 GB), version 0.1.0 per snapshot. Still called a Release Candidate in 2026 (*check*) | `tumbleweed` |
| os-release | `ID="opensuse-microos"`, `ID_LIKE` with `suse opensuse opensuse-tumbleweed microos` (*check*), `VERSION_ID` the snapshot | *check*: `ID="opensuse-aeon"`, or MicroOS's with a variant | Tumbleweed (`opensuse-tumbleweed`, `*`) |
| Root | btrfs, `/` mounted `ro` from fstab; the live root is `@/.snapshots/N/snapshot` with the btrfs `ro` property, as the btrfs default subvolume | the same, inside LUKS2 (`cr_root`, crypttab `none x-initrd.attach`) | Tumbleweed: the same subvolumes, root writable |
| `/etc` | a writable btrfs subvolume in each snapshot (t-u >= 5.0), mounted from fstab with `x-initrd.mount`; synced into the next snapshot at boot; `/etc/etc.syncpoint` | the same | Vanilla: an overlay with its upper in `/var` |
| Writable | `@/var` (with `C`), `@/root`, `@/home`, `@/srv`, `@/opt`, `@/usr/local`, `@/boot/grub2/*` (*check* the image's list) | the same, minus GRUB's; `/boot/efi` FAT | Tumbleweed |
| Changes | `transactional-update dup / pkg in / run CMD / rollback N`: tukit opens a snapshot of the default (or of the booted one), runs the command in it, closes it, and sets it as the default. State is in `/var/lib/misc/transactional-update.state` (kept across runs) and `/run/transactional-update.state` (6.2.0) | the same; desktop software as Flatpaks in `/var` | none: snapper's pre/post pairs are history, not the next root |
| Automatic updates | `transactional-update.timer` (daily) and `rebootmgr` | the same (*check* Aeon's reboot policy) | |
| Boot loader | the image's default: GRUB 2 with grub2-snapper-plugin (`/.snapshots/N/grub-snapshot.cfg`, the "read-only snapshot" submenu) and health-checker's `05_health_check` and `83_health_check_marker` scripts (the `health_checker_flag` in grubenv). On UEFI through SUSE's shim (*check*). `-grub-bls` and `-sdboot` variants of the same image also exist | systemd-boot through shim (*check*: shim), installed by `sdbootutil install`. Kernels and initrds are on the ESP; one BLS entry per snapshot and kernel, `rootflags=subvol=@/.snapshots/N/snapshot`; boot counting (`+3` in the file name) | Leap 16 (UEFI GRUB, shim); `chimera-uefi` (systemd-boot, no shim) |
| health-checker | GRUB path: `health-check.state` (`LAST_WORKING_BTRFS_ID`, `LAST_WORKING_SNAPSHOT`), `health-check.rebooted`, `grub2-editenv - set health_checker_flag=0`; on a failed first boot of a new default: `btrfs subvolume set-default LAST_WORKING_BTRFS_ID`, reboot | BLS path (`/boot/efi/loader/entries` exists): systemd-bless-boot's good/bad; on bad: `NEW_SNAPSHOT_FAILED=1`, `bootctl set-default ""`, reboot; on the next good boot, `sdbootutil set-default-snapshot` | none |
| Encryption | none (the image) | LUKS2 always (a systemd-repart bundle). **Default mode** when `/dev/tpmrm0` has PolicyAuthorizeNV: `sdbootutil enroll --method=tpm2`, PCRs 4,5,7,9 (`/etc/sysconfig/fde-tools`), plus a recovery key. **Fallback mode** without a TPM: a typed passphrase | `pikaos4-luks`, `ubuntu26-luks` (passphrase only) |
| SELinux | enforcing, targeted (*check* whether the image's first boot relabels, as Tumbleweed's install does) | *check* (Aeon may still be on AppArmor or enforcing SELinux) | Tumbleweed |
| Install | no installer: the `kvm-and-xen` qcow2, configured on first boot by **combustion** (a `combustion/script` on a disk labelled `combustion`) or ignition | **tik**: boots from the installer stick, deploys the image with systemd-repart, then runs its post modules (encryption, `sdbootutil install`). Unattended with `TIK_INSTALL_DEVICE` and `TIK_INSTALL_IMAGE` in `/etc/tik/config`; dialogs go through `cenity` when there's no Wayland/X session; the stick's IGNITION partition carries combustion/ignition files for the deployed system's first boot | Tumbleweed (AutoYaST), Leap 16 (Agama) |
| First boot | combustion's script; `jeos-firstboot` is not in the qcow2 (*check*) | gnome-initial-setup (makes the user), unless combustion made one (*check*) | |

## What works today, and what breaks

**bko-holt `dump`** (on the live system, as for every Linux guest):

- `osrel.rs` refuses both: no row. It needs `("opensuse-microos", "*",
  "openSUSE MicroOS")` and Aeon's (*check* the ID), with tests from the
  guests' os-release files.
- **Detection: `os.transactional`.** It is true when fstab mounts `/` `ro`,
  the root is a snapper slot with the `ro` property, and `tukit` or
  `transactional-update` is installed. It doesn't depend on the ID: SL
  Micro, Leap Micro and Leap 16.1's immutable mode would match too (none
  of them is tested here). Recorded beside it: the booted snapshot, the
  default subvolume (they differ while an update is pending), the
  t-u state file, health-checker's state, and the loader mode (GRUB or
  BLS).
- Btrfs, snapper's slots and the default subvolume are as Tumbleweed's. The
  new things in the subvolume list are the **nested `/etc` subvolumes**:
  one per slot, read-write, with `parent_uuid` naming the previous slot's
  `/etc` (*check*: t-u may create them by snapshot or by copy).
- **Aeon:** LUKS2 on the root partition with a `systemd-tpm2` token and a
  `systemd-recovery` keyslot. `probe/luks.rs` already parses tokens
  (`luks.rs:196`). The ESP holds the kernels and the BLS entries, and
  `detect_loader` should find systemd-boot through shim from BootCurrent
  (*check*: `\EFI\opensuse\shim.efi`).

**bko-holt `load`:**

- **format/restore:** btrfs with shared snapshots by
  docs/design/btrfs-snapshots.md approach A (`--clone-from`). New: a
  snapshot's nested `/etc` is made after its parent and before the parent
  is set `ro`, which is set last (`commands.rs:3227`, already last). IDs
  change, as that spec's "Known differences" says.
- **The boot stage today** would take the Fedora arm for `microos`: it
  runs `grub2-mkconfig` in the chroot, writing `/boot/grub2/grub.cfg`
  inside snapshot N. It may also run the initrd step (dracut), which
  writes `/boot/initrd-*` there, and the SELinux relabel step touches
  `/boot`. The snapshot comes up booting, but it no longer matches its own
  snapper metadata, and everything written is t-u's to own. `aeon` would
  take the existing `("systemd-boot", true)` via-shim arm (commands.rs:3129):
  it makes the boot entries and leaves everything else as restored. That
  is right, except for the initrd step, which must not run either.
- **health-checker's state** comes back byte-identical, with the source's
  ID: stale (above).
- **LUKS on Aeon:** `luksFormat` asks for a new passphrase (or uses
  `--key-file`, as the BS1 unattended LUKS runs do). The initrd's crypttab
  says `none`, so systemd-cryptsetup tries the tokens (none left) and then
  asks. With a typed passphrase it boots. TPM2 unlock is gone until it's
  re-enrolled.

**The rescue image:** no change ([[one-kernel-rescue]]). btrfs-progs,
cryptsetup and dosfstools are already in it; `sdbootutil`, `tukit` and
`grub2-*` run from the target, when they run at all.

## The design

### The transactional arm (boot stage)

When `os.transactional` is set, the boot stage **regenerates nothing in a
snapshot**. That means no `grub2-mkconfig`, no `grub2-install`, no
`shim-install`, no dracut, no `sdbootutil install` or `mkinitrd`, and no
relabel of the root. In order:

1. **Checks, not writes.** For GRUB, every `grub-snapshot.cfg` and the
   default's `grub.cfg` name the btrfs UUID that `format` kept and
   subvolume paths that exist. For BLS, every entry's `linux` and `initrd`
   exist on the ESP and its `rootflags=subvol=` names an existing slot.
   Every `root=UUID=`, `rd.luks.uuid=` and crypttab UUID is one that load
   kept. A failed check is a **problem** (load stops before the boot
   entry), not a silent regeneration. When the target differs from the
   source in a way that needs regeneration (another disk driver in the
   initrd, say), the plan says so and tells the user to run
   `transactional-update initrd` / `grub.cfg` on the restored machine.
   Load doesn't do it.
2. **health-checker's state**, GRUB path. If `/var/lib/misc/health-check.state`
   exists, `LAST_WORKING_BTRFS_ID` is rewritten to the restored ID of
   `LAST_WORKING_SNAPSHOT`'s path (read back with `btrfs subvolume show`
   after the restore), with a note giving both IDs. `health-check.rebooted`
   is kept as it was. If the path no longer resolves, that is a problem.
   The BLS path keeps no IDs, so nothing changes there.
3. **t-u's state** (`/var/lib/misc/transactional-update.state`) holds
   snapshot numbers, which are kept. It comes back as it was. A **pending
   update** (default ≠ booted on the source) stays pending: the restored
   machine boots the default, as the source's next boot would. The plan
   says which snapshot that is, and that health-checker will judge it.
4. **Boot entries** in NVRAM (`efi_entries()`), as for every UEFI arm.
5. **SELinux:** only `/var/log/bko-holt` (in `@/var`, writable) is
   relabelled. Nothing in a snapshot was written, so nothing there needs
   relabelling.
6. `ro` on the slots and their `/etc` as the source had them, last, as now.

What load must still write to the root (a changed fstab when the target's
layout differs, say) goes into the current slot's `/etc` subvolume, which is
writable on the source too. An fstab is the one case that can reach
`/etc/fstab`. The note names each file written there.

### Aeon's LUKS (Decision 4)

- **Recommended (a):** load re-creates LUKS2 as today (same UUID, same
  cipher and PBKDF settings, a typed passphrase or `--key-file`). The plan
  says plainly: "TPM2 unlocking and the recovery key are not restored. The
  restored system asks for this passphrase until `sdbootutil enroll
  --method=tpm2` (and `--method=recovery-key`) is run on it". The plan also
  keeps the source's `/etc/sysconfig/fde-tools` and PCR list, so the
  re-enrolment uses the source's choice. HoltTest1 does the re-enrolment
  over SSH after the first restored boot. It types the passphrase into
  sdbootutil's keyring, as tik does: `keyctl add user sdbootutil PW @u`.
  Then it reboots and requires an unlock with nothing typed.
- (b) A first-boot unit written by load that re-enrols by itself. This
  needs the passphrase on disk until it runs; rejected.
- (c) Keeping the LUKS header (`luksHeaderBackup`). It only works with the
  source's volume key, and block-level data restore isn't what bko does.
  Rejected.

### Detection order and other systems

`os.transactional` comes before the Fedora arm and the via-shim systemd-boot
arm. Tumbleweed and Leap 16 (not transactional) are unchanged, and a unit
test pins that. A Tumbleweed installed with systemd-boot (Tumbleweed's new
default for fresh installs) isn't tested by a guest. The via-shim arm and
its unit tests already cover it.

## Guests

### `microos` (M1)

- **Image:** `openSUSE-MicroOS.x86_64-16.0.0-kvm-and-xen-SnapshotYYYYMMDD.qcow2`
  (about 520 MiB), its `.sha256` and `.sha256.asc`, checked with `gpgv`
  against the openSUSE Project Signing Key (`AD485664...29B700A4`). The
  key isn't on an ISO here, so it is taken from the key file
  `make-opensuse.sh` already keeps (`opensuse-key.gpg`), or the repo's
  `repomd.xml.key` (*check*). Kept in WORK_DIR and in
  `/bkoscratch/rebuild-inputs/microos/` on Chopin and Cinqcent.
- **Disk:** the qcow2 copied and resized to 24 GiB before the first boot.
  The image's first boot grows the root (*check*: systemd-repart or KIWI's
  resize).
- **Firmware:** UEFI with Secure Boot on (OVMF's Microsoft-keyed store),
  the guest's vm-run options `--uefi --secure-boot` (*check* the
  spellings). Tumbleweed already covers openSUSE on BIOS. If the
  `kvm-and-xen` image boots only BIOS, the guest is BIOS, and `aeon`
  carries UEFI (*check*).
- **Combustion:** `testvm/opensuse/combustion/script`, filled by sed as
  `autoinst.xml` is. Its first line is `# combustion: network`. It sets
  root's hashed password and SSH key, enables `sshd`, adds the serial
  console to GRUB's command line (in the transaction combustion runs in),
  sets the hostname `microos`, and echoes `BACKOTTER-INSTALL-OK` to
  `/dev/ttyS0`. It goes on a small second disk that `mke2fs -d` makes
  without root, labelled `combustion` (*check*: the label combustion
  looks for, and whether FAT or ISO9660 is needed). `-no-reboot` isn't
  used: combustion runs in the first boot's initrd and the boot carries on.
- **Build:** `make-opensuse.sh microos SNAPSHOT WORK_DIR`. The mode does
  fetch and verify, copy and resize, then the first boot with the config
  disk, waiting for `BACKOTTER-INSTALL-OK` and SSH. Then come the relabel
  boot and the check boot, unchanged except that they run under OVMF and
  also record `findmnt -no OPTIONS /` (must have `ro`), `btrfs subvolume
  get-default /`, the booted slot, `health-checker` and
  `transactional-update.timer`'s state, and `mokutil --sb-state`. Then
  `qemu-img snapshot -c fresh-install`.
- **Settings for tests:** `transactional-update.timer` and `rebootmgr` are
  **disabled**, so an update never lands mid-test. health-checker stays
  enabled.
- **After the build:** one `transactional-update run touch /usr/share/bko-first`
  and a reboot, so the image has more than the installer's slots and a
  health-check state that names a slot the build made (a fresh image may
  have none: *check*).

### `aeon` (A1)

- **Image:** `Aeon-Installer.x86_64-0.1.0-SnapshotYYYYMMDD.raw.xz` and its
  signed `.sha256` (*check*: the `.asc` exists). Uncompressed (*check* the
  size), attached as a USB disk. tik filters out USB devices, which keeps
  it away from the target.
- **Target:** a 48 GiB qcow2 (*check* Aeon's minimum), UEFI with Secure
  Boot, and a **TPM 2.0** from swtpm (`vm-run --tpm DIR`, which exists:
  testvm/vm-run:116). The TPM's state dir travels with the guest
  (`aeon.tpm/` beside the qcow2, in vm-store's masters). Every boot of the
  guest, the rescuer and the restored disk uses a **copy** of it, so a run
  never changes the master's TPM.
- **Unattended tik:** before the first boot, the installer's
  `/etc/tik/config` gets `TIK_INSTALL_DEVICE=/dev/disk/by-id/virtio-aeon`
  and `TIK_INSTALL_IMAGE=` (the only image). How that is written is a
  *check*. Either the stick's root is writable (`guestfish`/`vm-mount` on
  the raw before the boot) or the installer itself reads combustion from
  its IGNITION partition. The pre modules (welcome, the GNOME environment)
  and the recovery-key dialog still show. With no Wayland session, tik
  uses `cenity` on the console, which `answer-popups.py` (or
  `console-boot.py`) answers with Enter on the serial line (*check*: the
  installer starts a GNOME kiosk and so a GUI; then the answers go in by
  QEMU's `sendkey`). The recovery key is read off the serial log and kept
  in WORK_DIR (`recovery-key`, mode 600), never in the repo.
- **First boot of Aeon:** the IGNITION partition on the stick carries a
  combustion script for the deployed system, as for `microos`: root's SSH
  key and sshd, a user `bko` so that gnome-initial-setup has nothing to ask
  (*check*), the serial console on the kernel command line through
  `/etc/kernel/cmdline` and `sdbootutil update-all-entries` (*check*), and
  `multi-user.target` as the default. The default is GDM, which costs RAM
  and isn't tested. Then the relabel and check boots, which also require
  an unlock with nothing typed (TPM2) and `systemd-bless-boot status` =
  `good`.
- **Settings for tests:** as `microos`'s, plus Flatpak's automatic updates
  off.

### vm-update through transactional-update (both)

`in-guest.sh` gains a `Linux.tukit` section, **before** `Linux.zypper`
(`in-guest.sh:621`, where `has zypper` would otherwise take it and zypper
would fail on the read-only root). It applies when `os.transactional`'s
test holds (fstab's `/` `ro` and `tukit` present):

1. `transactional-update --non-interactive --drop-if-no-change dup`. Exit
   codes and "nothing to do" are as zypper's (*check* how t-u reports
   them).
2. If a new snapshot was made, vm-update's clean reboot (already there)
   boots it, and health-checker must pass on it. A guest that rolled back
   by itself is a **FAIL** of the update, with health-checker's journal in
   the log.
3. After the reboot: `transactional-update cleanup`, plus `snapper cleanup
   number`, so that the slots stay bounded (t-u 6.2.0 sets no cleanup
   algorithm until `cleanup` runs). Then `zypper clean -a` inside a
   `transactional-update run`, or nothing if the cache lives in `/var`
   (*check*).
4. Aeon: `flatpak update -y --noninteractive` (system installation in
   `/var`) after the reboot, or nothing if no Flatpaks are installed. The
   guest installs none.

`fingerprint.py` learns the booted slot and the default (the fingerprint of
a transactional guest is its booted snapshot's packages, not the pending
one's). The `vm-update` header names both guests. GuestTest2 takes
`tumbleweed`'s rules, a new `S-tu` rule (booted = default after the update,
health-checker `good`, the slot count bounded), and `known-lines.json` lines
for t-u's output.

## Tests

**SticTest1** (`testvm/stictest1/microos`, `testvm/stictest1/aeon`): from
`tumbleweed`'s (btrfs subvolumes, snapper's read-only snapshots), plus
`chimera-uefi`'s `fatattr.py` for the ESP. The fixture is copied into
`/var/lib/bko-stic` (writable), because `/` is read-only. Real examples:
a read-only slot's `/usr` (set-id, capabilities, SELinux labels), a slot's
`/etc` subvolume, `/etc/etc.syncpoint`, `health-check.state`, t-u's state,
the ESP's shim and (Aeon) BLS entries with boot-count names and kernels.
Both archivers, both ZFS hosts, 0 FAIL. Registered in
`stic/manifests.json`, `limits.json` (as tumbleweed's), `check-manifests.py`'s
count and `tests/test_stic.py`'s.

**HoltTest1** (1Obscure, Decision 5). `HT_SOURCE_PREP` is a new
`prep/microos-tu.sh` (both guests). It makes the changes t-u makes in real
use:

1. three `transactional-update run` transactions, each changing `/usr`
   (files added, rewritten and deleted, as `tumbleweed-snapper.sh` does)
   and `/etc` (a file edited after the snapshot opened, so the
   `/etc` sync has work to do);
2. a reboot into the last one, so health-checker records it as good;
3. a **forced health-checker rollback**: a transaction that adds a failing
   plugin (`/usr/libexec/health-checker/bko-fail.sh`), a reboot,
   health-checker's rollback and its own reboot back to slot 2's
   successor. The prep requires that it happened (the journal's "do a
   rollback", or for BLS a `bad` entry);
4. `transactional-update rollback N` to an older slot, then a reboot, so the
   restored machine has a rolled-back default;
5. one more transaction, **not booted**, so the backup has a pending
   update (default ≠ booted). Last line `REBOOT` is left out: the dump runs
   with the update pending.

The restored disk must then:

- **boot the current snapshot.** It boots the pending one, as the source
  would have, and health-checker passes on it: `findmnt /` `ro`, booted
  slot = default, `getenforce` Enforcing (where the source was), no AVC
  denials beyond the known list (Leap 16's new boot-state line), and no
  failed units;
- **boot the older snapshots.** On `microos`, GRUB's "Start bootloader from
  a read-only snapshot" submenu is driven over the serial console
  (`console-boot.py`'s keystrokes) into the rolled-back slot and the
  oldest slot. On `aeon`, `bootctl set-oneshot ENTRY` from SSH does the
  same. Each must reach `running`, with its own `/etc` (a marker file per
  slot that the prep wrote);
- **keep health-checker consistent.** A new failing transaction on the
  restored machine, then a reboot: health-checker must roll back to the
  restored last-good slot (the default afterwards = that slot's restored
  ID, its path = the source's `LAST_WORKING_SNAPSHOT`). This is the test
  of the ID rewrite. Without it the rollback goes astray, so this check
  fails on today's load;
- **`/etc` handling:** each slot's `/etc` subvolume exists with the
  source's contents, nested where it was, read-write as on the source.
  `/etc/etc.syncpoint` matches. After the first restored boot,
  `transactional-update-sync-etc-state` ran with no error;
- **(Aeon) re-seal:** the first restored boot unlocks with the plan's
  passphrase (typed by console-boot.py, or the key file). HoltTest1 runs
  `sdbootutil enroll --method=tpm2` over SSH and reboots, and the second
  boot must unlock with nothing typed. Per [[front-load-manual-tests]],
  the typed-passphrase run goes first in a series.

judge.py: the transactional arm's promise is a **byte-identical** ESP,
`/boot` in every slot, and every slot's `/usr`. The rewritten
`health-check.state` is the one expected difference, with its new ID. The
ESP's BLS boot counters may change across the test's boots (*check*), and
the judge compares those entries against the files as they were before the
boot stage's own boot. A new boot-state helper reports the booted slot, the
default, health-checker's verdict and the loader's chosen entry.

**Test2:** golden plans in `holt/tests/corpus/microos/` and `.../aeon/` from
a `bko-holt dump` of each guest after its prep (so the plan shows the
transactional arm, the ID rewrite and, for Aeon, the TPM2 note).
Registrations: `src.json`, `plan.txt`, `index.tsv`, the corpus README;
`classes.json` (`uefi`, `H.Obscure`; `aeon` also `luks` and a new `tpm`
class if Test2 has none, *check*); `holt/spec.json` (`{"kind": "linux"}`);
`guest/spec.json` (tumbleweed's entry with the combustion or tik harness);
`estimates.tsv` (as tumbleweed's until measured); `t2lib/plan.py`'s source
and guest counts; `tests/fixtures/p2/world.py` and the re-blessed goldens.
Four other sessions are bumping the same counts this week, so each piece
takes the counts from `main` as it is when it lands, not from this spec.
Done when two corpus builds have the same sha256, leak scans are 0 on both
hosts and the Mac (the Aeon recovery key is in the leak scan's list), no
other plan changes, and Test2's own tests pass.

**BackShot1:** not proposed. The Thinkpad is in use for BackShot1All, and
Aeon's TPM sealing on real hardware is a later, separate test (Decision 7).

## Shared lines (with the other new-distribution sessions)

`holt/src/osrel.rs`'s `SUPPORTED`, `testvm/test2/classes.json`
(`H.Obscure`), the Test2 counts and p2 goldens, `holt/tests/corpus/index.tsv`,
`testvm/holttest1d/README.md`'s 1Obscure row and HoltTest1All, `ram-sizes.tsv`,
`stic/manifests.json`, `in-guest.sh` (a new section before `Linux.zypper`)
and `make-opensuse.sh` (Leap 16's mode switch). Each change goes in as its
own hunk on what `main` has then.

## What M1 found

**The image** (read offline 2026-11-14 with `vm-mount --ro` on Chopin):
`openSUSE-MicroOS.x86_64-16.0.0-kvm-and-xen-Snapshot20261111.qcow2`, 521 MiB
(20 GiB virtual), sha256 `4053c260...b1041b`. Its `.sha256.asc` is signed by
the openSUSE Project Signing Key (gpgv against tumbleweed's
`opensuse-key.gpg`: good). Kept in `/bkoscratch/rebuild-inputs/microos/`
on Chopin.

- os-release: `ID="opensuse-microos"`, `ID_LIKE="suse opensuse
  opensuse-tumbleweed microos sl-micro"`, `VERSION_ID="20261111"`. Kernel
  7.3.3-1-default.
- **GPT, hybrid BIOS + UEFI:** p1 BIOS boot (2 MiB, `p.legacy`), p2 ESP
  (20 MiB FAT, `p.UEFI`), p3 btrfs root (`p.lxroot`, the rest). The ESP
  holds only the fallback path, `EFI/BOOT/{bootx64.efi (shim), grub.efi,
  MokManager.efi, grub.cfg}`. The stub `grub.cfg` finds the btrfs by UUID,
  sets `btrfs_relative_path`, and sources `/boot/grub2/grub.cfg`.
- **fstab:** `/ btrfs ro=vfs`; `/.snapshots`, `/home`, `/opt`, `/root`
  (x-initrd.mount), `/srv`, `/var` (x-initrd.mount), `/usr/local`,
  `/boot/writable`, `/boot/grub2/{i386-pc,x86_64-efi}` as `@/...`
  subvolumes; `/boot/efi` vfat; and **`/etc /etc none bind,x-initrd.mount`**.
- **Subvolumes:** `@` (256), `@/.snapshots` (257),
  `@/.snapshots/1/snapshot` (258, `ro=true`, the default), the usual `@/*`,
  a new **`@/boot/writable`** (264, empty: GRUB's
  `btrfs-mount-subvol ... /@/boot/writable` mounts it as `boot_rw_subvol`,
  *check*: it holds the writable grubenv), and **`@/.snapshots/1/snapshot/etc`**
  (268, nested in 258, no `parent_uuid`). So the `/etc` design holds:
  a nested subvolume per slot, made writable by fstab's bind mount.
- GRUB: `grub.cfg` has grub2-snapper-plugin's snapshot menu and
  health-checker's `05_health_check`. The terminal is `gfxterm`: the build
  switches it to `serial console`, so the snapshot menu can be driven over
  the serial line in M5. The command line already has
  `console=ttyS0,115200 console=tty0 ignition.platform.id=qemu
  security=selinux selinux=1`.
- SELinux enforcing, targeted, with `/etc/selinux/.autorelabel` pending:
  the relabel boot is needed, as for Tumbleweed.
- Present: `/usr/sbin/{transactional-update,tukit,health-checker}`, dracut
  modules `30ignition`, `30ignition-microos`, `35combustion`, `77selinux`,
  `98selinux-microos`. health-checker's plugins: btrfs-subvolumes-mounted,
  etc-overlayfs, logind, rebootmgr, rpmdb-consistency, tmp. No sdbootutil.
  Enabled: sshd, transactional-update.timer, rebootmgr, health-checker.
- **Combustion** reads its script from `/dev/disk/by-label/{combustion,
  COMBUSTION,ignition,IGNITION,install,INSTALL}` **or from QEMU's fw_cfg,
  `opt/org.opensuse.combustion/script`**. No config disk is needed:
  `-fw_cfg name=opt/org.opensuse.combustion/script,file=script`. It runs
  the script inside `transactional-update shell` (a new snapshot, 2, that
  becomes the default), rolls back on a non-zero exit, and re-touches
  `.autorelabel` when the snapshot was relabelled.

**The build** (`make-opensuse.sh microos 20261111 vms/microos` on Chopin,
2026-11-14, under `cmp-run.sh --reserve 6 3`):

- **Key:** `repomd.xml.key` from `tumbleweed/repo/oss/repodata/` holds the
  pinned `AD485664...29B700A4`; gpgv over the `.sha256.asc` is good. The
  image, its `.sha256` and `.asc` live in `WORK_DIR/inputs/`, not beside the
  disk: vm-update (and compare.py) take every `*.qcow2` in the guest's
  folder for one of its disks, and the first `--try` failed on two.
- **Secure Boot under OVMF works:** `vm-run --uefi` (Microsoft-keyed store)
  boots the ESP's fallback shim, then GRUB; `mokutil --sb-state` says
  `SecureBoot enabled`. No `--no-secure-boot`. vm-run-options is `--uefi`
  alone; 2 GB is enough.
- **The root grows by itself** at the first boot: after `qemu-img resize`
  to 24 GiB, `/` is 25745666048 bytes of the 25769803776-byte disk before
  combustion's snapshot is even booted.
- **Combustion's snapshot is #2** ("Snapshot Update of #1", cleanup
  `number`), the default, booted at once in the same boot (no reboot):
  `@/.snapshots/2/snapshot`, ID 269. Snapshot 1 keeps no cleanup algorithm.
  The script needs no network, so it has no `# combustion: network` line.
- **`systemctl disable` alone doesn't hold:** the first boot (an
  uninitialized machine-id) runs `preset-all` after combustion and enabled
  `transactional-update.timer` and `rebootmgr` again (the first build
  failed on that). A preset of the build's own,
  `/etc/systemd/system-preset/00-backotter.preset` (`disable` both), plus
  the disable, keeps them disabled.
- **Relabel:** both relabels happen in the first boot. dracut's
  98selinux-microos relabels snapshot 1 before combustion runs, and the
  `.autorelabel` that combustion touches again is done in the same boot
  (`/etc/selinux/.relabelled` 4 s after the snapshot was made). The relabel
  boot then relabels nothing (no trigger, no relabel unit, 0 mislabelled),
  and neither does the check boot.
- **The check boot:** running, Enforcing, `/` `ro,...,subvol=/@/.snapshots/2/snapshot`,
  default = booted = `@/.snapshots/2/snapshot`, health-checker `active`,
  the timer and rebootmgr `disabled`. `health-check.state` after it:

  ```
  LAST_WORKING_BTRFS_ID=269
  LAST_WORKING_SNAPSHOT=@/.snapshots/2/snapshot
  ```

  So a fresh image does have a state, naming combustion's slot, and the
  extra transaction of "After the build" isn't needed for that.
- GRUB's menu now draws on the serial line (`openSUSE MicroOS`, "Advanced
  options", the firmware entries) once combustion's `grub2-mkconfig` ran;
  the image's own first GRUB says ``terminal `gfxterm' isn't found``.
- Build time 119 s (from the image in rebuild-inputs; the wait for the
  reservation not counted). `microos.qcow2`: 605 MiB on disk, 24 GiB
  virtual, snapshot `fresh-install`.

**vm-update through transactional-update** (transactional-update 6.1.3,
snapper 0.13.1, zypper 1.14.102, health-checker 1.13):

- `transactional-update --non-interactive --drop-if-no-change dup` keeps
  zypper's exit codes inside: 0, 102 and 103 become 0, anything else 1.
  With nothing to update it says `zypper: nothing to update`, discards the
  snapshot and exits 0, but leaves the snapshot's **empty directory** in
  `/.snapshots` (snapper's list doesn't have it), so the arm counts
  snapshots by `snapper --csvout list`. A 103 (zypper updated itself) is
  success to t-u; the rest of the update waits for the next vm-update.
- With an update (one package on 2026-11-14), it says `New default snapshot
  is #3 (/.snapshots/3/snapshot)`, exits 0, and warns that
  `/var/lib/openSUSE-build-key/imported` was written in the snapshot's
  `/var`, shadowed by the real one.
- zypper's cache is in `@/var` (`/var/cache/zypp`, 68 MiB after the dup):
  `zypper -n clean -a` works on the read-only root.
- **Cleanup:** `transactional-update cleanup` sets snapper's `number`
  algorithm and `important=yes` on the earlier working snapshots, but only
  those that are neither the current nor the booted one, so it has to run
  with booted = default. The image is only ever merged after vm-update's
  reboot, so the arm runs `transactional-update cleanup` and `snapper
  --no-dbus cleanup number` **first**, before the dup: that is the last
  update's after-reboot step, with no hook. snapper's root config has
  `NUMBER_LIMIT="2-10"`, `NUMBER_LIMIT_IMPORTANT="4-10"` and
  `NUMBER_MIN_AGE="3600"`, so the slots stay at about ten of each. A
  deletion is a `step:`.
- vm-update checks after its reboot that the snapshot booted is the one
  in-guest.sh's `tukit: default` line named, and that it is still the
  default: a health-checker rollback sets the default back and reboots
  into it, which booted = default alone wouldn't catch.
- fingerprint.py is unchanged: pkgset.sh's `rpm -qa` reads the booted
  snapshot's rpmdb, and `bko-holt dump` refuses `opensuse-microos` until M2.
- `vm-update --try --hosts '' microos` on Chopin: **ok** in 2 min 29 s.
  The cleanup marked #1; the dup made #3 (one package, a `step:` for the
  new default); after the reboot booted = default = `@/.snapshots/3/snapshot`
  as named; vm-mount reads the 12 file systems; 143 MiB overlay, thrown
  away. The rollback branch of the check is untested on the guest (M5's
  forced rollback will exercise it).

## What A1 found so far (the installer, read offline)

`Aeon-Installer.x86_64-0.1.0-Snapshot20261111.raw.xz`, 2.1 GiB (7.1 GiB
raw). Its `.sha256.asc` is signed by the openSUSE key (gpgv: good) and the
sha256 matches. Kept in `/bkoscratch/rebuild-inputs/aeon/` on Chopin, with
the raw unpacked in `/bkoscratch/aeon/installer.raw`.

- **The installer is itself an Aeon system,** and tik **self-deploys** it:
  `/usr/lib/tik/img` is empty, so tik copies the running stick.
  `usr/lib/repart.d/{00-esp,50-root}.conf` make an ESP (750M-4G, vfat) and
  a root with `Encrypt=key-file`. The root is btrfs, zstd:1, with the
  MicroOS subvolumes, `/@/.snapshots/1/snapshot:ro`,
  `/@/.snapshots/1/snapshot/etc` and `DefaultSubvolume=` slot 1. They are
  filled by `CopyFiles=` from the stick's `/`, minus `/ignition`, tik's
  sudoers and polkit rules, `/home/tik` and `/boot`. tik's post modules
  then do the rest: LUKS key, crypttab, `sdbootutil install`, TPM.
- os-release: **`ID="aeon"`**, `ID_LIKE="suse opensuse opensuse-tumbleweed
  opensuse-microos opensuse-aeon microos"`, `NAME="Aeon"`,
  `VERSION_ID="20261111"`. So the SUPPORTED row is `("aeon", "*", "Aeon")`.
- GPT: p1 BIOS boot, p2 ESP (750 MiB), p3 `p.lxignition` (ext4 at
  `/ignition`, empty), p4 btrfs root. fstab is MicroOS's, with
  `compress=zstd:1`, `/ ro=vfs` and the `/etc` bind mount.
- systemd-boot, installed by sdbootutil (`EFI/BOOT/{BOOTX64.EFI, grub.efi,
  MokManager.efi}`, `installed_by_sdbootutil`). One entry with a boot
  counter, `loader/entries/aeon-7.3.3-1-default-1+3.conf`:
  `rootflags=subvol=@/.snapshots/1/snapshot`, `root=UUID=`, `console=ttyS0`.
  Kernel and initrd are in `/aeon/7.3.3-1-default/` on the ESP, named by
  hash.
- SELinux enforcing. sdbootutil, health-checker and tpm2_getcap are present,
  and so are combustion and ignition (dracut modules). So a combustion
  script by fw_cfg on the installer's first boot can give it SSH, and tik
  can then be run over SSH.
- tik picks the disk by itself when there is exactly one, or by
  `TIK_INSTALL_DEVICE` (tik-core:147-197). It starts from GNOME
  (`/home/tik/.local/share/applications/org.opensuse.tik.desktop`); with no
  Wayland/X session it uses `cenity` on the terminal.

## What M4 found

SticTest1 `testvm/stictest1/microos` (2026-11-14, round rel8's Linux
binaries, `microos.qcow2` sha256 `77d002b0...8c96d4` on both hosts):

- **The guest has no python3** (nor attr, acl, libcap-progs, getcap):
  fsmeta.py, the shims and the judge need one. Installing it would mean
  `transactional-update pkg in` and a reboot on every run, so check.sh ships
  the host's python3.13 instead: the binary, its standard library (no
  tests, Tk, site-packages) and every library it and its extension modules
  link, with the host's glibc loader (20 MB tgz), into `/var/lib/bko-stic/py`;
  `/usr/local/bin/python3` runs it through the loader, and Python finds its
  prefix from where it runs. bkoshim.py stands in for setfattr, setfacl and
  setcapraw through wrappers in `/usr/local` (no `/usr/bin/python3` for its
  shebang).
- **Writable places:** the helpers go in `/var/lib/bko-stic`, the shims in
  `/usr/local` (`@/usr/local`). `/mnt` is on the read-only root, so inner.sh
  mounts a tmpfs over it (`rootcontext` = `/mnt`'s label, `mnt_t`) for the
  mount points.
- **The slots:** slot 2's `/etc` (ID 270) has `parent_uuid` = slot 1's
  `/etc` (268), so t-u snapshots `/etc` from the previous slot. The fixture
  builds `real/.snapshots/{1,2}/snapshot` the same way (slot 2 a snapshot of
  1 with a file added, one rewritten, one deleted; its `/etc` a snapshot of
  slot 1's), with the guest's `info.xml` and `grub-snapshot.cfg`, and sets
  both slots `ro` last. The slots' `/usr` carry the image's 13 set-id files
  and its 3 files with capabilities (newuidmap, newgidmap, clockdiff), 27
  distinct SELinux labels. Both tools back them up and restore them as
  directories, exactly, in all four directions. `/etc/etc.syncpoint` doesn't
  exist on this image (t-u 6.1.3). health-check.state
  (`health_checker_var_lib_t`) and transactional-update.state come back
  exactly.
- **The ESP** is FAT16, 512-byte sectors, 4 sectors a cluster, 20 MiB,
  mounted `fmask=0077,dmask=0077,codepage=437,iocharset=iso8859-1,shortname=mixed`;
  the scratch FAT is made and mounted the same. The guest's shim, GRUB,
  MokManager and stub grub.cfg (FAT attributes 0x20, directories 0x10) come
  back byte-identical with their attributes and times.
- **Two FAT LIMITs, as kinoite44's:** the snapshot's top ancestor `/mnt`
  carries a SELinux label that a FAT target can't take (each restore warns
  for that directory only), and the source FAT's root has no times (1970),
  which a directory on FAT gets as 1980-01-01. Classed in limits.json with
  the btrfs ones (project IDs, the new subvolume's unlabelled root, birth
  times).
- **Results:** 44 PASS, 0 FAIL, 6 LIMIT for both archivers (restic-bko and
  rustic-bko, all four directions, restore over existing, each tool's
  `check --read-data` of the other's repository, the non-root run) on
  Cinqcent (st1-m4b: 233 s once its reservation came, 374 s with the wait)
  and Chopin (st1-m4a: 223 s, 19 min with the wait behind other sessions).
  No archiver bug found.

## What M5 found

HoltTest1 `microos` (2026-11-14; bko-holt from main's tree with the
transactional arm and the fixes below, static, `HT_HOLT`; rel6's rustic-bko;
the UEFI rescuer `rescuer-uefi.qcow2`, sha256 `215e0f79...e567` on both
hosts; `microos.qcow2` `77d002b0...8c96d4` on both hosts, copied to
Cinqcent's `vms/` on /bkoscratch/fast by rsync).

- **The prep** (`testvm/holttest1d/prep/microos-tu.sh`, ht1.sh's default
  `HT_SOURCE_PREP` for `microos*` and `aeon*`) takes 4 rounds and about 4
  minutes: ht1.sh's new `AGAIN` protocol reboots the source between rounds
  and waits until a boot settles (running or degraded, the same boot ID
  3 s later). The forced rollback's failing boot (slot 6) lasts about 9 s:
  health-checker runs after logind, logs "Machine didn't come up correctly,
  do a rollback", sets the default back to slot 5's ID and reboots before
  sshd answers. The result: slots 1-7, booted 3 (`transactional-update
  rollback 3`), default 7 (pending), slot 6 with the failing plugin,
  health-check.state `LAST_WORKING_BTRFS_ID=273` / `@/.snapshots/3/snapshot`.
- **`transactional-update` facts:** `--continue` chains a transaction on
  the default (without it each is based on the booted slot, and t-u warns
  that the previous default's changes are not in it); a run from a slot
  booted from the snapshot menu works and is based on that slot.
  `rollback N` sets N as the default directly (no new snapshot) and drops
  N's cleanup algorithm. Each new slot's `/etc` has an `etc.syncpoint`
  **subvolume** nested in it (writable), which
  transactional-update-sync-etc-state deletes at the slot's first boot,
  after syncing the old `/etc`'s later changes into it ("Using new
  snapshot - syncing from old parent ..."). Slots never booted (4, 7) keep
  theirs. M2's restore handles the nesting (three levels).
- **The snapshot submenu** lists slots newest first, as
  `/.snapshots/grub-snapshot.cfg` sources them; a slot's entry loads that
  slot's `grub.cfg`, whose menu says "Help on bootable snapshot #N" and boots
  its default after 10 s. On the serial line the highlighted entry is drawn
  in reverse video (`ESC[7m`), which `grub-menu.py` reads; arrows are sent
  as GRUB's `v` and `^`, since an arrow's escape sequence split by a busy
  host was read as Escape and left the submenu (Cinqcent). Slot 1 (the
  image's own root) has `terminal gfxterm` in its `grub.cfg`, so its menu
  doesn't draw on the serial line; it boots, but without sshd or the key
  (combustion set them up in slot 2), so it is checked on the console (its
  login prompt, host name `localhost`: its own `/etc`); slot 2 is the oldest
  slot checked over SSH.
- **A booted slot isn't saved by health-checker** unless it is the
  default (`save_working_snapshot`), so a submenu boot leaves
  health-check.state as it was, and a failing transaction made from such a
  boot tests the state load left (the rollback test).
- **The harness's backup was the wrong slot** (first runs `aeon1`,
  `aeon1c`): `vm-mount` mounts the btrfs default at `/`, which on a source
  with an update pending is the pending slot, while the live system (and a
  backup made on it, as a user's is) has the booted one there, as bko-holt's
  dump and load assume. Load restored slot 3 from the backup's `/`, so slot
  3 came back with slot 7's files. Fixed in the harness: `vm-mount`'s
  `VM_MOUNT_BTRFS_ROOT`, set by ht1.sh for the backup and the file check from
  the dump (`tu_mount_root`). Not a bko-holt defect.
- **bko-holt fixes** (holt/, with unit tests; `cargo test` passes, no golden
  plan changed):
  1. The image is hybrid (BIOS boot partition with GRUB's core, `boot.img`
     in the MBR), and the transactional arm wrote no BIOS boot code: the
     restored disk had none (`boot_code.kind` grub2 -> none). Load now
     writes the source's own `boot.img`/`core.img` from the restored
     `@/boot/grub2/i386-pc` (a writable subvolume, not a snapshot) with the
     source's `grub2-bios-setup`, run by its own dynamic loader and
     libraries from the restored tree (no chroot), else the rescue's
     `grub-bios-setup` (the UEFI HoltTest1 rescuer has none).
  2. sfdisk writes a GPT's protective MBR with a zero disk signature; KIWI's
     image has `922b9cd8`. Load writes the source's (bytes 440-443) after
     sfdisk when it isn't zero (every Linux layout; no corpus source has
     one, so no golden plan moved).
  3. The booted slot's fstab mount points (`/var`, `/home`, `/.snapshots`,
     ...) are hidden in the backup under their mounts, so load made them
     unlabelled; a boot of that slot from the submenu then had AVC denials
     (systemd-sysctl's getattr on `unlabeled_t` dirs). The boot stage now
     labels each one that has no label with the same directory's label in
     another slot, else the mounted file system's root's.
- **What a file-level restore can't keep:** files transactional-update
  wrote into the booted slot's own `/var` (shadowed by `@/var` on the
  running system: `var/cache`, `var/lib/zypp`, ... 11 paths in slot 3) are
  not in a backup of the running system, so they don't come back.
  `judge.py fs` names them; nothing reads them while `@/var` is mounted.
- **The ID rewrite** wasn't exercised: load made slot 3 with the source's
  ID (273), so `LAST_WORKING_BTRFS_ID` was kept as it was. The rollback test
  still passed through it (the default after health-checker's rollback is
  273, `@/.snapshots/3/snapshot`). Slot 7 got ID 286 (284 on the source);
  `judge.py` compares slots and what is nested in them by path.
- **Checks added:** `testvm/holttest1d/ht1-tu.sh` (the source's baseline;
  the restored first boot: booted = default = the source's default, `/`
  read-only, SELinux, health-checker passed and saved it, no failed unit or
  new AVC denial, the `/etc` sync; health-check.state as load left it; the
  rolled-back slot, the base slot and slot 1 from the submenu; the
  post-restore health-checker rollback), `grub-menu.py`, judge.py's
  transactional rules (`TU_STAGE`/`TU_HELD`, `transactional_judge`, slots'
  nested subvolumes by path, `<FS_TREE>/` dropped) and their judge-test.py
  cases. BLS (Aeon) has hooks only: `tu_boot_slot_bls` and the BLS rollback
  stop with a FAIL naming A4.
- **Results:** PASS on both hosts with bko-holt 8f5e153bb741 (a static
  build of this tree, `HT_HOLT`), under `cmp-run.sh --reserve 4 3` in
  /mnt/bko-ram, port 2695. Cinqcent: `aeon2c`, a whole run from `source`,
  714 s (source 191 s with the prep, backup 53, rebuild 142, verify 327),
  RAM-disk peak 2.7 GiB (in rebuild), anon peak 2.7 GiB; rustic-bko of the
  shipped set then (`afb5593e...`). Chopin: `aeon1`, the run's
  `verify` again after the fixes (its `backup` and `rebuild` with them
  already), PASS; RUNS_CHOPIN_FULL. 0 file differences (34 excused: load's
  log, the slot-3 alias's mount points, the 11 hidden `/var` paths), 0
  dump differences. `ram-sizes.tsv`'s row: D 4, MEM 3. The source dump of
  the pass, for M3's golden plan:
  `cinqcent:/bkoscratch/holttest1d/aeon2c/microos/dumps/src.json` (bko-holt
  8f5e153bb741's dump, after the prep).
- **Earlier attempts** (kept, FAIL, on /bkoscratch): `aeon1` (Chopin) and
  `aeon1c` (Cinqcent, `microos`, `.1`-`.3`) found the items above in order:
  the wrong slot backed up, `who` unset in ht1-tu.sh, `grub-menu.py`'s
  `$(...)` that never returned (its stdout held), the BIOS boot code, the
  protective MBR's signature, no `grub-bios-setup` in the UEFI rescuer, the
  snapshot check's `btrfs filesystem du /`, the arrows' Escape, slot 1
  without sshd, the unlabelled mount points, a rerun's stale console log;
  and one harness stop, ht1.sh changed under a running run by another
  session's sync (`syntax error near unexpected token`, Chopin).

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| M1 | **`microos` guest.** Fetch the qcow2 and its signed `.sha256` into rebuild-inputs on Chopin (the user's OK, ~520 MiB). Read it offline (`vm-mount`: os-release, fstab, the subvolume list with `/etc`'s, the loader, shim, health-checker's and t-u's versions and states, SELinux). Then `make-opensuse.sh`'s image mode, the combustion script and config disk, the first boot, the relabel and check boots, the extra transaction, `rebootmgr` and the timer off, `rebuild-one.sh`'s `microos)` line, copy to Cinqcent (same SHA-256), `in-guest.sh`'s `Linux.tukit` section and `vm-update --try`. Settle every MicroOS *check* and write "What M1 found" here. Done when `microos` boots alone, ro, enforcing, with Secure Boot on, on both hosts, and `--try` is ok | 1.25 d |
| M2 | **bko-holt (High-effort subagent):** the `SUPPORTED` rows and tests; `os.transactional` in dump (booted and default slot, the state files, the loader mode); nested `/etc` subvolumes in the btrfs plan (parent first, `ro` last); the transactional arm (checks instead of regeneration, health-check.state's ID rewrite, the pending-update note, no initrd step, no root relabel), ahead of the Fedora and via-shim arms; unit tests from M1's layout, plus Tumbleweed and Leap still on their arms. Done when `cargo test` passes and no other golden plan changes | 1.25 d |
| M3 | `microos` golden plan and Test2 registrations (as Leap's L3) | 0.4 d |
| M4 | `microos` SticTest1 entry and runs: both archivers, both hosts, 0 FAIL, under `cmp-run.sh` reservations in /mnt/bko-ram | 0.35 d |
| M5 | `prep/microos-tu.sh`, the boot-state helper, judge rules (byte-identical slots and ESP, the health-check.state difference), GRUB-menu boots of older slots by console keys, the post-restore health-checker rollback; HoltTest1 1Obscure runs of `microos` on Chopin and Cinqcent; RAM peaks in `ram-sizes.tsv`; finished guests moved to disk | 0.8 d |
| A1 | **`aeon` guest.** Fetch the installer raw (the user's OK, 2.2 GB). Read it offline (tik's config and modules as shipped, the IGNITION partition, the image bundle, os-release). Then unattended tik under OVMF + Secure Boot + swtpm, the recovery key kept, the first boot's combustion, the relabel and check boots (TPM2 unlock, bless-boot good), `rebuild-one.sh`, vm-store carrying the TPM state, copy to Cinqcent, `vm-update --try`. Write "What A1 found". Done when `aeon` boots alone, unlocking by TPM, on both hosts | 1.5 d |
| A2 | bko-holt (High): Aeon's os-release row; BLS-entry checks in the transactional arm; the TPM2/recovery-key note and kept `fde-tools`; unit tests | 0.4 d |
| A3 | `aeon` golden plan, Test2 registrations (`luks`, `tpm`?), SticTest1 entry and runs | 0.6 d |
| A4 | HoltTest1 runs of `aeon` on both hosts: the passphrase boot (front-loaded), the re-seal and unattended boot, the `bootctl set-oneshot` boots of older slots, the BLS health-checker rollback | 0.5 d |
| M6 | Docs: holt/README.md's supported list, `osrel.rs`'s comment, HoltTest1's 1Obscure row and HoltTest1All, SticTest1's and Test2's READMEs, `layload.md`'s boot stage (the transactional arm), memory (holttest1d index, an aeon-support note) | 0.15 d |
| | *≈ 7.2 d for both; ≈ 4.2 d for `microos` alone (M1-M6 without A1-A4)* | |

M1-M5 come before A1, since Aeon reuses the transactional arm, the prep and
the judge rules. Each piece is committed on main when its tests pass and the
user says to commit, as its own hunks; nothing is pushed until the user says
so.

**Costs** (est.): M1 a few minutes' build (no installer) at `--reserve 6 2`
in /mnt/bko-ram, WEAR_TAG `build:microos`. A1 a 20-40 min tik install at
`--reserve 12 4`, WEAR_TAG `build:aeon`. qcow2 ~2-3 GiB (`microos`) and
~8-10 GiB (`aeon`) a host. Cinqcent is the fullest disk (~69%), so only
masters and caches go there, and run overlays are deleted on a pass. M4,
M5, A3 and A4 are about 1 h of queue a host each. No time of the user's
but the OKs.

## Risks

- **Aeon's unattended install is the least settled part.** tik is built
  for a person at a GNOME screen. If neither its config nor cenity can be
  driven from the serial line, A1 falls back to QEMU `sendkey` with
  screenshots, est. +0.5 d. If even that is unreliable, the option is to
  deploy tik's own systemd-repart bundle with tik's post modules run by
  hand. That is no longer "what a user gets", and I'd ask before doing it.
- **Aeon is still a Release Candidate** and changes fast (tik moved from dd
  to systemd-repart, and Aeon from GRUB to systemd-boot, within a year).
  The pinned installer keeps the guest reproducible, and a later rebuild
  may need A1's harness reworked.
- **The images' history isn't kept** by openSUSE. Rebuilds use the copy in
  rebuild-inputs. vm-update keeps the guest current.
- **Nested `/etc` subvolumes in read-only parents** are new to the btrfs
  plan. If approach A's `--clone-from` can't take them (a nested
  subvolume shows as an empty directory in its parent's snapshot), M2
  grows (est. +0.5 d).
- **BLS boot counting** renames ESP files as the guest boots, so "byte-
  identical ESP" has to be judged against the right moment.
- **swtpm and Secure Boot on all four hosts:** cage and copland run
  neither guest unless swtpm is installed there (*check*). Both guests are
  ZFS-free, so all four could host them.
- **SELinux:** a relabel left pending by combustion or tik would redo
  itself on every throwaway boot, as Tumbleweed's did. The relabel boot
  exists for that.
- **health-checker on the restored first boot:** if the restored machine
  failed a check, health-checker would itself roll back to the last good
  slot. That would look like success unless the judge requires booted =
  default = the expected slot.

## Out of scope

- MicroOS or Aeon systems still on the **`/etc` overlay** (migrated from
  before t-u 5.0): dump refuses them with a note (Decision 6).
- The `-grub-bls` flavour; MicroOS's DVD install (YaST/Agama); SL Micro,
  Leap Micro and Leap 16.1's immutable mode (they would take the same arm,
  but have no guest); Kalpa (KDE: same as Aeon for bko).
- Restoring the TPM2 seal or the recovery key; FIDO2 tokens.
- arm64; changes to the rescue image; BackShot1/BackShot2 on these systems.
- XBOOTLDR (openSUSE's July 2026 option of a separate boot partition):
  noted if A1 finds Aeon uses it (*check*), otherwise later.

## Decisions for the user

1. **The guests:** both `microos` (GRUB, snapshot menu, health-checker's
   GRUB path, no encryption) and `aeon` (systemd-boot BLS, health-checker's
   bless-boot path, LUKS2 with TPM2). Recommended: between them they cover
   both loaders and both health-checker paths, and Aeon's FDE default is
   what its users have. Or **`microos` only** (~4.2 d; the transactional arm
   and the health-checker rewrite are all tested, but not BLS or FDE). Or
   `microos` plus a MicroOS `-sdboot` variant in place of Aeon (+1 d over
   microos-only; BLS without tik or LUKS).
2. **MicroOS's build:** the official `kvm-and-xen` qcow2 with a combustion
   disk (recommended: it is MicroOS's documented VM path, and quick). Or
   the MicroOS DVD with AutoYaST, as Tumbleweed's (an installer layout,
   but a 30 min install and YaST's MicroOS role, *check* that it's still
   offered).
3. **The boot stage on transactional systems:** regenerate nothing and
   check instead (recommended: the snapshots stay what t-u made and
   SELinux labels stay whole, as on Vanilla). Or remake `grub.cfg` and the
   initrd in the current slot before `ro` is set, as Tumbleweed's arm does
   (less new code, but it changes a snapshot behind t-u's back).
4. **Aeon's FDE after a restore:** re-create LUKS with the passphrase, say
   so in the plan, and have HoltTest1 re-enrol the TPM (recommended, (a)
   above). Or build `aeon` in Fallback mode (no swtpm, a typed passphrase
   only), which tests nothing new for LUKS.
5. **HoltTest1's word:** **1Obscure**, beside `tumbleweed` and `leap16`
   (recommended: the same choice as Leap 16, with no new word). Or a new
   **1S** (SUSE: `tumbleweed`, `leap16`, `microos`, `aeon`), which is
   tidier, but more churn in the README and `classes.json` while four
   other sessions edit them.
6. **`/etc` overlay systems** (MicroOS from before 2025): refuse in dump
   with a note (recommended: new installs and the images have used
   subvolumes for almost two years, and t-u migrates old ones). Or support
   the overlay too, the way Vanilla's is handled (+0.75 d, and a guest
   built from an old image that isn't served any more).
7. **BackShot1:** none (recommended: the Thinkpad is busy with
   BackShot1All, and real-TPM sealing is its own test). Or Aeon on the
   Thinkpad after A4 (+0.5 d and about 1 h of the user's time).
8. **Downloads:** the MicroOS qcow2 (~520 MiB) and the Aeon installer
   (2.2 GB) with their signed checksums to Chopin's rebuild-inputs
   (recommended). Or the user fetches them.
