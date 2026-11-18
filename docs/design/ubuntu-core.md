# Ubuntu Core 24 support (all-snap, recovery seed, TPM-sealed FDE)

2026-11-14. The user asked for this as one of five new distros, each picked
for a restore path that no current guest tests. Ubuntu Core 24 is the
all-snap Ubuntu. It has no root filesystem of its own: the root is the
`core24` base snap. It has four fixed partitions, a recovery seed it can
reinstall from, and full-disk encryption sealed to the TPM. The immutable
precedents are Bazzite (docs/design/bazzite.md) and Vanilla OS
(docs/design/vanillaos.md). The encryption precedents are the
`ubuntu26-luks` guest and BackShot1's unattended unlock
(docs/design/bs1-luks-unattended.md). **The user approved this spec on
2026-11-14 with every recommended decision** (the last section). Nothing
is built yet.

**Conventions.** est. marks an estimate. *Check* marks a fact the sources
don't settle. Each one is settled on the first guest (U1), before the code
that depends on it. The facts were read on 2026-11-14 from:
- Ubuntu Core's docs: full-disk-encryption, use-a-recovery-mode,
  recovery-modes, storage-layout, how-installation-works, console-conf;
- `canonical/pc-gadget` branch 24, `gadget/gadget-amd64.yaml`;
- `cdimage.ubuntu.com/ubuntu-core/24/stable/current/` and its
  `.model-assertion`;
- `canonical/snap-tpmctl`'s README.

Facts marked *(snapd)* come from what is known of snapd's source. They are
not from a page fetched today, so each one is also a *check*. Nothing was
downloaded or installed. Code references are to local `main` at d5e8686.

## Summary

- **The release.** Ubuntu Core 24, as asked. It is an LTS with
  standard support to 2029, and its base is 24.04. The docs now say
  "Latest: Ubuntu Core 26". Decision 1 offers UC26 too, or instead.
- **The layout is fixed by the pc gadget.** On GPT:
  - a 440-byte MBR and a 1 MiB BIOS Boot partition;
  - **ubuntu-seed**: vfat ESP, 1200M;
  - **ubuntu-boot**: ext4, 750M;
  - **ubuntu-save**: ext4, 32M;
  - **ubuntu-data**: ext4, grown at install to fill the disk.

  With FDE, ubuntu-save and ubuntu-data are LUKS2 volumes; seed and boot
  are never encrypted.
- **Most of the system lives in files on those four partitions.**
  - The snaps are squashfs files: `/var/lib/snapd/snaps/*.snap` on data,
    plus the seed's pool on ubuntu-seed.
  - The running kernel's `kernel.efi` sits on ubuntu-boot.
  - snapd's own state is in data's `modeenv` and `state.json` and in
    save's device keys.

  So a file-level restore that keeps every filesystem's UUID and label,
  and every partition's GPT name and PARTUUID, gets the system back.
  **Nothing is regenerated**, as on ABRoot. Every snap revision and every
  recovery system survives as a file.
- **The hard part is FDE.** bko-holt `load` today runs `luksFormat`, which
  makes a **new volume key** (`holt/src/load/commands.rs:1143`). The keys
  sealed to the TPM then unlock nothing, and neither does the recovery
  key. **Recommendation (Decision 2): keep the LUKS2 header.**
  1. dump saves each LUKS2 header with `cryptsetup luksHeaderBackup`.
     This needs no key.
  2. load writes the header back with `luksHeaderRestore`, opens the
     volume with the user's recovery key and restores the files inside.

  The volume key, every keyslot and every token come back. Each restore
  then makes one of two promises, by machine:
  - **The same machine (the same TPM and the same Secure Boot
    variables, `dbx` included):** it boots unattended, and the TPM
    unseals as before. The boot chain (shim, GRUB, `kernel.efi`, the
    command line, the model) is byte-identical, so the PCRs match.
  - **A new machine (a new TPM, or reset firmware variables): the
    recover-mode limit** (the user's Decision 2b, 2026-11-14). Run mode
    can't unlock and reboots in a loop without asking for the key. Recover
    mode asks for the recovery key on the screen and unlocks the data, so
    the user can reach or copy it. A normal boot there needs a reinstall
    of Ubuntu Core: Ubuntu Core 24 doesn't re-provision onto a different
    TPM (U1 tested it).
- **The unencrypted guest** is restored file-exact and boots unattended
  on any machine.
- **Two guests from the stock image:** `uc24`, installed with no TPM (so
  unencrypted), and `uc24-fde`, installed with swtpm and Secure Boot on
  (so encrypted). They come from the same signed
  `ubuntu-core-24-amd64.img.xz`. The first boot is set up unattended by a
  NoCloud CIDATA drive (Decision 3). vm-run already has `--tpm DIR` and
  `--uefi-vars`.
- ~6.5 d (est.), with no BackShot1.

## Why

- **No root filesystem.** Every other guest has a `/` on a block device.
  On Ubuntu Core, `/` is a squashfs loop mount of `core24_N.snap`, and
  the writable parts are bind mounts out of `/writable/system-data`.
  These all break if they follow mounts or fstab as they do now: dump,
  vm-mount, HoltTest1's backup and compare.py. Ubuntu Core has no fstab
  for the four partitions: snap-bootstrap in the initramfs mounts them by
  label.
- **TPM-sealed keys.** Only one guest uses a TPM today, and it seals no
  disk key. Restoring to the same machine versus a new one changes the
  answer, and a backup tool must say which promise it makes.
- **Encrypted volumes that must keep their key.** Every current LUKS
  restore makes a new volume key and asks for a new passphrase. Here, a
  new key would break sealing, the recovery key and snapd's boot-chain
  bookkeeping all at once.
- **A recovery seed.** ubuntu-seed holds whole recovery systems that the
  device can reinstall or factory-reset from. A restore that loses one
  has lost the machine's rescue path.
- **Revisions.** snapd keeps the previous revision of each snap (default
  `refresh.retain`) and can `snap revert` offline. A restore keeps that
  history, or `revert` breaks.

## What Ubuntu Core 24 is (as far as bko cares)

| | Ubuntu Core 24 (pc-amd64, stock model) | Nearest supported system |
|---|---|---|
| Image | `ubuntu-core-24-amd64.img.xz`, 448M, with `SHA256SUMS` and `SHA256SUMS.gpg` in `cdimage.ubuntu.com/ubuntu-core/24/stable/current/`. The listing's date is **2024-06-03**, so the first boot refreshes almost every snap (*check* for a newer dated build). A raw disk image, not an installer ISO | Haiku's raw image |
| Model | `ubuntu-core-24-amd64`, brand `canonical`, **grade signed**, no `storage-safety` (so: encrypted when the hardware can, else not). Snaps: `pc` (gadget, 24), `pc-kernel` (24), `core24`, `snapd`, `console-conf` (optional) | |
| Disk (gadget) | GPT, from `gadget-amd64.yaml`: `mbr` 440 B; `BIOS Boot` 1M at 1M; `ubuntu-seed` vfat 1200M, ESP type, role system-seed; `ubuntu-boot` ext4 750M; `ubuntu-save` ext4 32M (min 16M); `ubuntu-data` ext4 1G, grown to the disk at install. ubuntu-data must be last. The partition names are GPT names, and snap-bootstrap finds the partitions by filesystem label (*check* labels on the encrypted ones: `ubuntu-data-enc` and `ubuntu-save-enc` on the LUKS2 header, `ubuntu-data` and `ubuntu-save` inside, *(snapd)*) | elementary8's GPT, but no LVM |
| Boot | UEFI: shim and GRUB on ubuntu-seed (`EFI/boot/bootx64.efi`, the removable path; *check* whether an NVRAM entry is made at all). The seed GRUB's `grubenv` picks the mode (`snapd_recovery_mode`, `snapd_recovery_system`). In run mode it chains the run GRUB on ubuntu-boot, whose `grubenv` names `snap_kernel` and `kernel_status` (try/trying while a new kernel is tested). The kernel is a UKI-like `kernel.efi` extracted from `pc-kernel_N.snap` into `ubuntu-boot/EFI/ubuntu/pc-kernel_N.snap/`. BIOS boots through the BIOS Boot core.img (`bootcode.rs` already handles that kind of partition) | Pop!_OS's kernelstub (extracted kernels), Bazzite's static grub.cfg |
| Modes | **run** (normal); **recover** (data untouched, from a seed system); **install** (from the seed: re-creates boot, save and data); **factory-reset** (re-creates data, keeps save). `snap reboot --run/--recover/--install/--factory-reset LABEL`; `snap recovery` lists the systems ("Label Brand Model Notes") | none |
| Seed | `ubuntu-seed/systems/<label>/` (model, assertions, `kernel/`, options) for each recovery system, plus a shared `snaps/` pool. The stock image ships one label. More can be made through snapd's REST API ("create a recovery system", used by remodel and validation sets, *check* the API shape in U1) | none |
| FDE | Needs UEFI Secure Boot and TPM 2.0. The key is sealed "to the full EFI state, including the kernel command line", and snapd measures the model too. Without a valid key, the boot prompts "Please enter the recovery key for disk /dev/disk/by-partuuid/…" on the console, one prompt per encrypted partition. The recovery key is eight 5-digit groups (`snap recovery --show-keys`) and "will remain the same". The PCR set and policy (*(snapd)*: PCRs 4, 7 and 12 under a PolicyOR over the allowed boot chains, with an NV policy counter in the TPM) and where the sealed keys live (*(snapd)*: files in `ubuntu-boot/device/fde/` and `ubuntu-seed/device/fde/` on older snapd, LUKS2 tokens with named keyslots on newer snapd) are both *check*s. The keep-the-header design works either way | ubuntu26-luks (typed passphrase); none with a TPM |
| Recovery key as LUKS key | *(snapd)* The key is 16 bytes: each 5-digit group is one little-endian uint16. The keyslot holds those raw bytes, not the dashed string, which matches a community report that the shown key "doesn't directly function" with cryptsetup. *Check* in U1: bko-holt gets a converter (below) | |
| Writable | `ubuntu-data`: `system-data/` (the writable paths: `etc/` pieces, `var/lib/snapd/{snaps,state.json,modeenv,seed,device,…}`, `var/snap/<name>/<rev>` snap data) and `user-data/` (homes). `ubuntu-save`: `device/` (the serial's private keys, the FDE marker *(snapd)*). Each snap's writable data is per revision, so `revert` brings the old data back | ABRoot's /etc upper |
| Read-only | Every `.snap` (squashfs), the base, the kernel snap's extracted `kernel.efi` | ostree's objects |
| Running mounts | `/run/mnt/ubuntu-seed`, `/run/mnt/ubuntu-boot`, `/run/mnt/ubuntu-save`, `/run/mnt/data` (= `/writable`), `/snap/<name>/<rev>` loop mounts, `/var/lib/snapd/save` binds | |
| os-release | From the base snap: *(check)* `ID=ubuntu-core`, `VERSION_ID="24"`, `NAME="Ubuntu Core"`. Offline, dump reads it from `core24_N.snap` (named by `modeenv`'s `base=`) with `unsquashfs -cat` | |
| First boot | install mode runs from the seed (it partitions, encrypts if it can and copies the snaps), reboots to run mode, then `console-conf` on tty asks for the network and an Ubuntu One email to import SSH keys. "A development tool". It does not run if a user already exists (*check*) | Vanilla's first-setup |
| cloud-init | By grade (a UC20 forum table, *check* for UC24): ubuntu-seed's `data/etc/cloud/cloud.cfg.d` **dangerous only**; a **NoCloud CIDATA drive dangerous and signed**; the gadget's `cloud.conf` all grades. After the first boot, snapd restricts cloud-init's datasources | |
| Updates | `snapd` auto-refreshes four times a day. A kernel or gadget refresh reboots into try mode, and snapd reseals the FDE keys for the new boot chain before the reboot. `snap refresh --hold` (no names) holds the auto-refreshes, and a manual `snap refresh` still works | Bazzite's uupd |

## The hard part: what a restore promises under TPM-sealed FDE

What snapd checks at boot, and what a restore must keep for each check to
pass:

1. **The TPM unseals the run key** only if the PCRs match one allowed boot
   chain and the TPM holds the same storage key and policy counter. So:
   shim, both GRUBs, `kernel.efi` and both `grubenv`s byte-identical, the
   same Secure Boot variables, the same TPM.
2. **The unsealed key opens the LUKS2 volume** only if the volume still
   has the **same volume key** and its keyslot. So: the header is kept.
3. **snap-bootstrap pairs data and save** through a marker in each
   *(snapd)*, and by partition/label on the boot disk. So: the files are
   kept, and the labels, UUIDs and PARTUUIDs too.
4. **snapd's `modeenv`** lists the trusted boot assets' hashes and the
   current kernels. So: the files on ubuntu-boot and ubuntu-seed are kept.

Options considered:

| | Same machine | New machine (new TPM) | Cost | Verdict |
|---|---|---|---|---|
| **A. Keep the LUKS2 header** (`luksHeaderBackup` at dump, `luksHeaderRestore` at load, opened by the recovery key) | Unattended boot | Recover mode only, with the recovery key typed on the screen; run mode loops (U1) | New `keep_header` path in load, header blob in the backup, recovery-key converter | **Recommended** |
| B. A new header whose passphrase is the recovery key (load's generic path, `--key-file` = the 16 converted bytes) | Run mode loops, because the sealed key opens no slot; recover mode only | Same | Little: the converter only | The fallback if A has a flaw. It also keeps a promise that A can't: a **new volume key** for a user whose old disk was stolen (Decision 2's option) |
| C. bko reseals for the target's TPM itself (predicting the PCRs with secboot) | Unattended | Unattended | Re-implements snapd's security-critical sealing. It breaks on every snapd change | Rejected |
| D. Factory-reset or reinstall from the seed, then restore data on top | Unattended | Unattended | Destroys what the restore is for (data is re-created). snapd's state no longer matches the restored files | Rejected |
| E. Unencrypted only, refuse FDE | n/a | n/a | None | Rejected: FDE is the default on real hardware with a TPM |

**Decision 2's recommendation is A**, with these promises written in
holt/README.md and in the plan's notes:

- **uc24 (unencrypted):** restored file-exact. It boots unattended on any
  machine with UEFI, or with BIOS through the gadget's core.img.
- **uc24-fde onto the same machine** (same TPM, same Secure Boot
  variables: PK, KEK, db, dbx, SbatLevel):
  restored file-exact, with both LUKS2 headers byte-identical. It boots
  unattended **only if nothing was resealed since the backup** (U2/U3
  review): a kernel, gadget or snapd refresh, or a new recovery system,
  after the backup reseals the keys and advances the TPM's NV policy
  counter, which revokes the sealed-key objects the backup holds; run mode
  then boot-loops as on a new machine, and `--new-machine` is the way in.
  Loading needs the recovery key (`--key-file`): the rescue system's boot
  chain is not the one the key is sealed to, so the TPM never unseals for
  bko-holt (dumping, in the running system, needs none: U2).
- **uc24-fde onto a new machine: the recover-mode limit** (Decision 2b).
  The same restore. Run mode reboots in a loop, so `load --new-machine`
  writes `snapd_recovery_mode=recover` into the restored seed's
  `grubenv` for the newest good recovery system that asks for the key
  (U1: the image's system did, the newer one didn't, *check* why in U3).
  The first boot is then recover mode: it asks for the recovery key on
  the screen, unlocks both volumes, and the data is readable. A normal
  boot on that machine needs a reinstall of Ubuntu Core; bko doesn't
  reseal. The plan's note and holt/README.md say so in those words. It is
  a LIMIT in HoltTest1, not a FAIL.
- **Secure Boot off on the target, reset firmware variables, or another
  firmware with other db/dbx:** the same as a new machine. A dbx update
  on the same machine has the same effect, for Ubuntu Core with or without
  bko.
- **The recovery key itself is never stored by bko.** The user keeps it,
  as Ubuntu Core tells them to. The backup holds only what the source
  disk held: the LUKS2 headers, with key material wrapped by the
  keyslots, and the sealed-key objects. Inside the encrypted data volume,
  snapd may keep `recovery.key` and `ubuntu-save.key` *(snapd)*, so the
  backup repository holds those as file contents. They are inside
  rustic-bko's encrypted repository like every other file. The plan,
  the dump JSON, the corpus and the logs name them only by path, and
  `redact.rs` gets a rule that a recovery-key-shaped string (eight
  groups of five digits) never reaches a plan or a bug report.

**What this costs bko-holt (generic, usable beyond Ubuntu Core):**

- dump: `luks.header` (a file in the dump directory, its sha256 in the
  JSON) for each LUKS2 device on an Ubuntu Core system. Opt-in elsewhere
  with `--keep-luks-header DEV`, and the default stays a new header.
- load: a `keep_header` arm in `luks()`. It checks that the target's
  container is at least as big as the source's data segment needs, runs
  `luksHeaderRestore`, opens with `--key-file`, and runs no `luksAddKey`
  or token changes. It refuses with `--new-uuids` (the header carries the
  UUID) and with a LUKS1 header (Ubuntu Core is LUKS2 only).
- `bko-holt recovery-key-file DIGITS OUT`: writes the 16 bytes. It reads
  the digits from stdin, never from argv, so they don't sit in `ps` or
  the shell's history. It prints only the converted key's sha256 prefix.
  The rescue image's docs show it before `dump` and `load`.

## The design

### One tree convention: snap-bootstrap's mount points

As ABRoot used its own mount points, bko uses the run mode's: ubuntu-seed
at `/run/mnt/ubuntu-seed`, boot at `/run/mnt/ubuntu-boot`, save at
`/run/mnt/ubuntu-save`, and data at `/run/mnt/data`. The backup's tree is
those four roots, and nothing under `/snap`, `/` or the bind mounts. dump
lists them in `storage.backup_sources` with `mount` hints (V2's
`MountHint`), so a live backup from the running system and an offline one
from the rescue image back up the same paths.

### Detection

An Ubuntu Core system is a disk with all of the following:
- a GPT holding the four partitions by name;
- an ext4 `ubuntu-boot` with `device/model`, or an `EFI/ubuntu/grubenv`
  holding `snap_kernel`;
- a `ubuntu-data` (plain, or opened) with
  `system-data/var/lib/snapd/modeenv`.

dump reads `modeenv` for `mode`, `recovery_system`,
`current_recovery_systems`, `good_recovery_systems`, `base`, `gadget`,
`current_kernels` and `model`. It reads the model assertion (brand, model,
grade, storage-safety) and os-release from the base snap. A new
`holt/src/snapcore.rs` holds the parsing and `boot.snapcore` in the
report. `osrel.rs` gets the row `("ubuntu-core", "24", "Ubuntu Core 24")`.
It is appended, and `supported_names`' assert string is appended to, not
overwritten (the shared lines).

### `dump`

- **Refuses** (v1):
  - mode not `run`;
  - `kernel_status` `try` or `trying`, or `snap changes` in progress,
    since a live backup would be mid-refresh: a plan note says to wait;
  - a non-pc gadget;
  - FDE without the volume opened (it names the
    `recovery-key-file` step).
- **Records:**
  - the four partitions with GPT names, type GUIDs, PARTUUIDs, attributes
    and exact sizes;
  - each filesystem's UUID and label, and the vfat volume ID;
  - the LUKS2 headers (above);
  - the recovery systems with their labels and the seed's snap list;
  - the installed snaps with every revision (`state.json`'s sequence, or
    the `.snap` files when state can't be read);
  - the BIOS Boot partition's bytes (bootcode.rs) and the MBR's 440
    bytes.
- **Live backups:** snapd may refresh mid-backup. The plan's note says to
  `snap refresh --hold` first and `--unhold` after. dump warns if
  `/run/snapd` shows a change in progress.

### `load`

- **Partitions:** recreated by the gadget's own geometry from the dump:
  - each GPT name, type GUID, PARTUUID and size exact;
  - ubuntu-data may grow under `--fit-largest`, since it is last and the
    install grows it anyway;
  - seed, boot and save are never resized.
- **Filesystems:** `mkfs.vfat -i VOLID -n ubuntu-seed`, and ext4 with the
  source's UUID, label and features (as every ext4 restore does).
- **FDE:** Decision 2's `keep_header`, then ext4 inside with the source's
  inner UUID and label.
- **Files:** restored into the four roots by the archiver, with mode,
  owner, xattrs and times as usual. vfat has no owners; seed's file times
  are kept as bko keeps any vfat's.
- **Boot stage (`snapcore_boot`):** regenerates nothing: no `grub-install`
  and no `update-grub`.
  1. It checks the tree: `modeenv`'s `current_kernels` each have
     `ubuntu-boot/EFI/ubuntu/<kernel>/kernel.efi`, the run `grubenv`'s
     `snap_kernel` is one of them, and each of `current_recovery_systems`
     has `ubuntu-seed/systems/<label>/model`.
  2. It makes an NVRAM entry only if the source had one (*check*), else
     it notes that the firmware boots the removable path.
  3. It writes the core.img to BIOS Boot and the MBR code (bootcode.rs).
  4. It writes its log and the tail.
- **The marker** in `restored_marker()` and status.rs: `modeenv`.
- **Refused in v1:** `--new-uuids` (snap-bootstrap and the sealed-key
  objects name partitions); a target smaller than seed+boot+save plus
  data's used space; arm64 and other gadgets; UC20 and UC22, which differ
  in the key layout (*check*, maybe a later row).

## Guests

All of them come from the stock signed image, which is fetched into
`/bkoscratch/rebuild-inputs/ubuntu-core24/` on Chopin with its SHA256SUMS
and the `.gpg`, verified with Ubuntu's cdimage key. The user's OK comes
before the download. The disk is **8G** sparse: the stock image `dd`'d
onto a qcow2 and grown. `vm-run-options` holds only `--uefi --memory
2048`. The swtpm state (`tpm/`) and OVMF variables (`uefi-vars.fd`) are
part of the guest, and `vm-store` carries `tpm/` as a sidecar (U1). But
every boot of `uc24-fde` must run on **copies** of both, passed as
`--uefi-vars COPY --tpm COPY`: store objects are read-only, and a boot
that reseals advances the TPM's NV counter, which the kept disk would no
longer match. Teaching `rebuild-one.sh`'s boot check, compare.py and
ht1.sh those copies is U4's.

- **`uc24`:** installed with **no TPM** and Secure Boot on (OVMF
  `secboot` + MS vars, through shim). Not encrypted.
- **`uc24-fde`:** installed with **swtpm** and Secure Boot on, so the
  install encrypts. Its recovery key is read once at build time with
  `snap recovery --show-keys` over SSH and written to
  `/bkoscratch/rebuild-inputs/ubuntu-core24/uc24-fde.recovery` (mode
  0600) on each host. It is **never in the repo**: a testbed secret, like
  BackShot1's key file on mybig512.
- *Optional (Decision 4):* `uc24-bios`, the same image under SeaBIOS
  (the gadget's BIOS Boot path). It is never encrypted.

**How they're built (Decision 3):**

1. **The stock image plus a NoCloud CIDATA drive** (recommended).
   `testvm/make-ubuntu-core.sh FLAVOR IMG WORK_DIR` boots the image under
   QEMU with a second disk labelled `CIDATA` holding:
   - `user-data`: a user `tester` with the farm's SSH key and a generated
     password, `ssh_pwauth: false`, and `console=ttyS0` added through
     `snap set system system.kernel.cmdline-append`, *check* that the
     stock gadget allows it, else no serial console after first boot and
     HoltTest1 waits on SSH alone;
   - `meta-data`.

   The install mode runs, reboots to run mode, and cloud-init makes the
   user. console-conf then has nothing to do (*check*). The script waits
   for SSH, then:
   1. `snap refresh` (the 2024 image is stale) and the reboots that
      follow;
   2. `snap refresh --hold`;
   3. creates a **second recovery system** from the refreshed snaps
      through snapd's API (U1 settles the call), so the seed has two
      labels;
   4. installs one small app snap (`hello`), refreshes it once so it has
      two revisions, and writes data under `/var/snap/hello/` for each
      revision;
   5. writes `/run/mnt/data/bko-mark` and `/run/mnt/ubuntu-save/bko-mark`
      (`/var/lib` is the base's read-only squashfs, U1);
   6. for `-fde`, reads the recovery key into the host file above;
   7. powers off. Prints BACKOTTER-INSTALL-OK.
2. **A model of our own built by `ubuntu-image`, grade dangerous**, with
   cloud-init on ubuntu-seed. This is the fallback if signed grade
   refuses the CIDATA drive. It needs a signing key registered to the
   user's Ubuntu One account (`snapcraft create-key`, `register-key`):
   the user's step. The key stays on the build host and out of the repo.
   The guest is then not the stock signed image, so the FDE path is the
   same but the grade differs. est. +0.75 d.
3. **console-conf driven on the serial console** with an Ubuntu One
   email whose SSH keys are on Launchpad. Network-dependent and tied to
   the user's account. Not recommended.

**Room** (est., *check* in U1): seed ~0.6 GiB, data ~1.5-2.5 GiB after
the refresh (two revisions of each snap), boot ~0.1 GiB. The qcow2 is
~3 GiB, and HoltTest1's D is ~8 GiB: it fits the RAM disk on every host.
Small enough for **cage and copland** too, if they have `swtpm` and
`swtpm_setup` (*check*; the guest needs no OpenZFS).

**Guest tools:**

- **vm-mount, snapcore mode:** when the GPT has the four names, mount
  them at the four run-mode paths under MP. For `-fde`, open ubuntu-data
  and ubuntu-save with the host's recovery key file through the
  converter. vm-chroot is refused: there is no root to chroot into. A
  `--squash-root` option mounts the base snap with data's binds for
  read-only checks, *est.* only if HoltTest1 needs it.
- **vm-update, a snapcore branch:** `snap refresh` (all snaps, the
  kernel and snapd included), wait for `snap changes` to settle across
  the reboots, `snap refresh --hold` again, then `qcow2-punch.py`. Under
  FDE, snapd reseals for the new kernel before its reboot, so the
  refreshed `uc24-fde` must boot unattended. That is vm-update's own
  check. For both guests:
  - `fingerprint.py`'s `LINUX_KINDS` gets `snapcore`;
  - `pkgset.sh` lists `snap list --all` (name, revision, tracking);
  - the guest rule `K-snapcore` in `t2lib/guest_judge.py` (kernels as
    `modeenv`'s `current_kernels`, each with its `kernel.efi`).

## Tests

**SticTest1** (`testvm/stictest1/uc24`): a scratch disk laid out like the
gadget:
- vfat with a long file name and the ESP type;
- ext4 boot and save;
- LUKS2 data whose header holds a `systemd-tpm2`-like token and two
  keyslots.

Checks:
- both archivers, both hosts;
- the files in all four roots, and squashfs `.snap` files byte-exact;
- the vfat volume ID and labels;
- the LUKS2 header byte-identical after load's `keep_header`;
- the token readable.

The `stic/manifests.json`, `limits.json` and `check-manifests.py` counts
are one session at a time (the shared lines).

**HoltTest1** (Decision 5: **1U**): ht1.sh backs up through vm-mount's
tree, loads onto a blank disk, and boots the restored disk alone. The
cells:

| Cell | Guest | Target firmware and TPM | Must show |
|---|---|---|---|
| a | `uc24` | fresh OVMF vars, no TPM | Run mode over SSH, with each of the following equal to the source's: `snap list --all` (every revision), `snap recovery` (both labels), the `modeenv` lines, and both `bko-mark`s. `snap revert hello` works offline. |
| b | `uc24` | the same | A boot into the **second** recovery system in **recover** mode (`snap reboot --recover LABEL`, then `--run`) reaches SSH in recover mode, then run mode again |
| c | `uc24-fde` | **the source's** swtpm state and OVMF vars, copied | Unattended boot: no recovery-key prompt on the serial log, SSH up, cell a's checks, and `snap recovery --show-keys` the source's key |
| d | `uc24-fde` | a **new** swtpm dir and fresh OVMF vars, loaded with `--new-machine` | The LIMIT: the first boot is recover mode; the key is typed on tty1 through QMP (`qmp.py`) only once the prompt shows in a screen grab; then SSH in recover mode with `degraded.json` naming ubuntu-data unlocked and both marks readable. Then `snap reboot --run` must loop as U1 saw (recorded, not a FAIL) |
| e | `uc24-fde` | the source's TPM | `vm-update`'s kernel refresh on the **restored** disk, then an unattended boot (snapd's reseal works on a restored system) |
| f | `uc24-fde` | the source's TPM, copied **after** a reseal | Back up; refresh the kernel on the source (snapd reseals, the TPM's policy counter advances); restore the **old** backup onto a blank disk and boot it on that TPM. Record what happens: expected a run-mode boot loop (the restored sealed keys revoked), then recover mode by `--new-machine` |

judge.py **requires** these to be byte-identical to the source's:
- everything on ubuntu-seed and ubuntu-boot;
- both LUKS2 headers;
- every `.snap`;
- `modeenv` and `state.json`.

It allows `var/log/bko-holt`, the journal, snapd's timers and the
`/var/lib/snapd` churn that a boot writes (the list is settled in U7).
**Done for fsmeta 2026-11-18** (U6): the record is of vm-mount's tree,
taken before the restored disk boots, so only load's log is allowed, at
`run/mnt/data/system-data/var/log/bko-holt` with its `var/log`'s times;
seed, boot, the `.snap`s, `modeenv` and `state.json` are held byte for
byte. The LUKS2 headers are `uc24-fde`'s cells (not built).
`ram-sizes.tsv` gets rows. [[front-load-manual-tests]] doesn't apply,
because cell d types the key from a file. Runs: Chopin and one other host
per guest.

**Test2:** the golden plans in `holt/tests/corpus/{uc24,uc24-fde}/`
(`src.json`, `plan.txt`, `index.tsv` rows, the corpus README). They show
the four GPT names, the `luksHeaderRestore` lines and no `grub-install`,
and the leak scan must find no recovery-key-shaped string. Also:
`classes.json` (U family, `uefi`), `holt/spec.json`, `guest/spec.json`,
`estimates.tsv`, `t2lib/plan.py`'s counts with their README and tests,
`world.py`'s `GUESTS`, and the re-blessed p2 goldens. These go in the
sibling sessions' **one batched Test2 pass**, as Vanilla's and PikaOS's
do. A `snapcore` scenario in `scenarios.rs` and `keep_header` unit tests
come with U3.

**BackShot1:** not proposed. It needs the Thinkpad reinstalled with
Ubuntu Core, and its TPM is real. That would be the only test on a real
TPM (Decision 6 offers it as U9).

## Shared lines (with the other new-distro sessions)

- `osrel.rs` `SUPPORTED` gets one appended row. The second session to
  land rebases.
- `redact.rs`'s lists (`GENERIC_LABELS` + `ubuntu-seed`, `ubuntu-boot`,
  `ubuntu-save`, `ubuntu-data`, `-enc` forms; the recovery-key rule) are
  re-read before editing.
- `classes.json`, the holttest1d README's 1U row and
  `check-manifests.py`'s count: one session at a time.
- `holt/src/load/commands.rs` `luks()` is the line most likely to
  overlap: Qubes (LUKS on its dom0) and TrueNAS (ZFS native encryption,
  a different function) are in flight. A note goes in the shared folder
  before U3 starts, so the `keep_header` arm is written once and the
  others reuse it.
- vm-run's `--tpm` and the store's handling of swtpm state are shared
  with any TPM guest (Qubes may want one).

## What U1 found

**The build** (2026-11-14, Chopin, in /mnt/bko-ram under `cmp-run.sh
--reserve 10 3`, then moved to `~/backotter-work/vms/`):
`testvm/make-ubuntu-core.sh FLAVOR IMG WORK_DIR`, with its helpers in
`testvm/ubuntu-core/` (`user-data`, `probe.sh`, `disk-facts.sh`,
`new-tpm-test.sh`, `recover-new-tpm.sh`, `type-recovery-key.py`, `qmp.py`,
`recovery-key-bytes.py`). `rebuild-one.sh` has the `uc24` and `uc24-fde`
lines. `uc24` took 677 s and `uc24-fde` 993 s, from the image to the
second check boot. Each guest's `build.txt` holds every probe, and
`disk-facts.txt` the host-side GPT, labels and LUKS2 headers.

- **Both guests boot alone, SSH works, and `uc24-fde` boots unattended
  twice.** The builder's two check boots use a qcow2 overlay and copies
  of the guest's `uefi-vars.fd` and `tpm/`. Both reach run mode over SSH
  with no recovery-key prompt. `uc24` also boots from `vms/` with
  `vm-run --ram --uefi --memory 2048` and fresh variables.
- **The install.** The image holds only BIOS Boot and ubuntu-seed. Install
  mode makes ubuntu-boot, ubuntu-save and ubuntu-data in ~40 s, and
  ubuntu-data grows to fill the 8 GiB disk (6207 MiB). With no TPM:
  `install.go:207: not encrypting device storage as checking TPM gave:
  ... no TPM2 device is available`. With swtpm: `TPM device detected and
  enabled`, `encryption: cryptsetup`. Run mode answered on SSH 82-128 s
  after the start.
- **The refresh.** The image's snapd 2.63 can't take today's kernel: its
  first auto-refresh fails with `cannot parse snap.yaml: unknown component
  type "standard"` (pc-kernel 3908). The builder's own `snap refresh`
  takes snapd first, then the rest, in one change, with its reboots.
  After it: snapd 2.78 (28332), core24 20261022 (2307), pc 196,
  pc-kernel 6.8.0-149.149 (3908, 7 components), console-conf 79. `hello`
  pulls in `core` and `core20`. snapd reboots with a delay: `ssh` says
  "System is going down" (pam_nologin) for minutes first. `snap refresh
  --no-wait` prints only a change ID, even for "no updates": `snap refresh
  --list` says "All snaps up to date".
- **hello:** revision 42 (latest/stable, 2.10), then 29 (latest/beta,
  2.10.1), with data in `/var/snap/hello/42/bko-data` and
  `/var/snap/hello/29/bko-data-b`.
- **The marks:** `/run/mnt/data/bko-mark` and
  `/run/mnt/ubuntu-save/bko-mark`. **Not `/var/lib/bko-mark`**: `/var/lib`
  is the base snap's squashfs, read-only (`tee: /var/lib/bko-mark:
  Read-only file system`), with only snapd's writable paths bound over it.
- **Users.** cloud-init's `hashed_passwd` fails: `chpasswd -e` writes
  `/etc/passwd` (`cannot lock /etc/passwd`), which is the base's, and the
  failure ends cc_users_groups before sudo is set up. `useradd
  --extrausers` itself works. So `user-data` has no password, and the
  builder writes tester's hash into `/var/lib/extrausers/shadow` over SSH.
  `disable_root: false` gives root the key (`/root/.ssh/authorized_keys`),
  so root logs in as on every other guest. `snap recovery` (the list) now
  needs root: `access denied` as tester under snapd 2.78.

The *checks*:

- **Labels** (blkid in the guest, and `disk-facts.sh` from the host on
  64 MiB copies of each partition, `qemu-img dd`):
  - GPT names `BIOS Boot`, `ubuntu-seed`, `ubuntu-boot`, `ubuntu-save`,
    `ubuntu-data`. Seed is the ESP type, the rest Linux data.
  - plain: vfat `ubuntu-seed` (LABEL and LABEL_FATBOOT), ext4
    `ubuntu-boot`, `ubuntu-save`, `ubuntu-data`.
  - FDE: the LUKS2 labels are **`ubuntu-save-enc`** and
    **`ubuntu-data-enc`**; inside, ext4 `ubuntu-save` and `ubuntu-data`.
    The dm names are `ubuntu-data-<uuid>` and `ubuntu-save-<uuid>`, a new
    UUID each boot (neither the LUKS nor the ext4 UUID).
  - **The image fixes some IDs for every install:** the seed's vfat volume
    ID `ABA0-ACE2` and the PARTUUIDs of BIOS Boot (`2f78214f-...`) and
    ubuntu-seed (`954a6c14-...`) are the same on both guests and in the
    image. Boot, save and data get new PARTUUIDs and UUIDs per install.
- **Where the sealed keys live: in files, not LUKS2 tokens.** The install
  ran snapd 2.63, and 2.78 keeps that layout after the refresh. Both
  LUKS2 headers have **no tokens** and two keyslots (`cryptsetup
  luksDump` on the host copies): keyslot 0, priority `preferred`, argon2i
  with 32 KiB (the TPM-unsealed key), and keyslot 1, `normal`, argon2i
  with 791438 KiB (the recovery key). The files (names and sizes only):
  - `ubuntu-boot/device/fde/ubuntu-data.sealed-key` (131898 B);
  - `ubuntu-seed/device/fde/ubuntu-data.recovery.sealed-key` and
    `ubuntu-save.recovery.sealed-key` (131331 B each);
  - `ubuntu-save/device/fde/{marker, tpm-lockout-auth,
    tpm-policy-auth-key}`;
  - `ubuntu-data/system-data/var/lib/snapd/device/fde/{boot-chains,
    recovery-boot-chains, marker, sealed-keys, ubuntu-save.key,
    recovery.key}`. `recovery.key` (16 B) appears only after the first
    `snap recovery --show-keys`.

  The sealed-key files are rewritten on each reseal (12:49, after the
  recovery system). The LUKS2 data segment starts at 7 MiB (7340032 B:
  2 MiB metadata twice, 2.5 MiB keyslots), so a `luksHeaderBackup` is
  7 MiB a volume.
- **The recovery key as a LUKS key: 16 raw bytes, yes; the dashed string,
  no.** `cryptsetup open --test-passphrase -v --key-file` on the host
  copies: the 16 bytes from `recovery-key-bytes.py` (each 5-digit group a
  little-endian uint16) give `Key slot 1 unlocked` on both ubuntu-data and
  ubuntu-save. The dashed string as is fails: `Command failed with code -2
  (no permission or bad passphrase)`. U2's converter is right as specified. Keyslot 1's
  argon2i asks for ~773 MiB of memory: the rescue image needs that much
  free to open either volume.
- **A new machine (a new swtpm dir and fresh OVMF vars): run mode never
  asks for the recovery key; it reboots in a loop.** `new-tpm-test.sh`
  and screen grabs (`qmp.py screendump`; the prompt would be on tty1)
  show, in each round of about 30 s:
  `snap-bootstrap: Error with keyslot "external:legacy": cannot recover
  keys from keyslot: invalid key data: cannot load sealed key object into
  TPM: ... TPM_RC_INTEGRITY ... Either the sealed key object is bad or the
  TPM owner has changed`, then `cannot activate encrypted device
  ".../b93c93b3-...": no valid keyslots and / or no more passphrase, PIN
  or recovery key tries remaining`, `[FAILED] Failed to start
  snap-initramfs-mounts.service`, and a reboot (`panic=-1`) into run mode
  again: the seed's grubenv still says `run`. 50 grabs over 150 s: five
  rounds, no prompt.

  So **the spec's new-machine promise is wrong as written**: the first
  boot does not ask for the key. A restored `uc24-fde` on a new TPM boots
  in a loop until something boots recover mode instead.

  **The new-machine tests, settled 2026-11-14** (`recover-new-tpm.sh`,
  results in Chopin's `/bkoscratch/u1-ubuntu-core/rec2`, `rec3`). Keys at
  the firmware splash land in OVMF's setup, so the script sets the
  overlay's seed `grubenv` to `snapd_recovery_mode=recover` for a given
  system (what `snap reboot --recover` writes) before the first boot.
  In run mode `snapd_recovery_system` is empty, so it names one.
  - **Recover mode of the image's system (`20240603`, snapd 2.63)
    asks for the key and unlocks both volumes** with it on tty1. The
    journal: the TPM load fails, then `successfully activated encrypted
    device ... using` the recovery key, for ubuntu-data and ubuntu-save.
    `degraded.json` says ubuntu-data is mounted. SSH answers as tester:
    the data is reachable.
  - **Run mode afterwards still loops.** After `snap reboot --run`, run
    mode fails as before ("the TPM owner has changed", "no more ...
    recovery key tries remaining"), 18 grabs over 180 s. **Ubuntu Core 24
    does not re-provision onto a different TPM** after a recovery-key
    boot. The docs' "restore the TPM from the recovered key" doesn't
    cover a new TPM.
  - **Recover mode of the newer system (`bko-20261114`, snapd 2.78)
    never asks for the key.** It reaches a `localhost login:` prompt on
    tty1 with ubuntu-data locked, so tester doesn't exist and SSH never
    answers. The key the script typed blind went into the login field.
    Those grabs were deleted, and the script must type only at a prompt
    it has seen (U6).
  - **Fresh firmware variables break the same TPM too**
    (`new-tpm-test.sh --same-tpm`, `rec-sametpm`): every boot loops. The
    guest's `dbx` is 17,912 bytes, and OVMF's stock `OVMF_VARS_4M.ms.fd`
    has 76. `uc24`'s variables have the same updated `dbx`, so FDE didn't
    cause it, and none of our scripts writes `dbx`. PCR 7 measures `dbx`,
    so **"the same machine" means the same TPM and the same Secure Boot
    variables** (PK, KEK, db, dbx, SbatLevel). With both copied, it boots
    unattended (twice on Cinqcent, `/bkoscratch/u1-ubuntu-core/u1-cinq`).
  - Cell d can't use console-boot.py either way: the prompt is on tty1.
- **os-release** (core24 423 and 2307 alike): `NAME="Ubuntu Core"`,
  `VERSION="24"`, `ID=ubuntu-core`, `PRETTY_NAME="Ubuntu Core 24"`,
  `VERSION_ID="24"`. The spec's guess holds.
- **NVRAM.** The install makes **no** entry: its reboot goes through
  OVMF's `Boot0001 "UEFI Misc Device"` (the removable path,
  `EFI/boot/bootx64.efi`). The refreshed gadget (pc 196) adds shim's
  fallback: `EFI/boot/fbx64.efi`, and `EFI/ubuntu/{shimx64.efi,
  grubx64.efi, bootx64.csv}`. The next boot through the removable path
  runs `fbx64.efi`, which writes **`Boot0007 "ubuntu"
  HD(2,...)/\EFI\ubuntu\shimx64.efi`**, first in BootOrder (virt-fw-vars
  on the kept `uefi-vars.fd`). With fresh variables, the same happens on
  the first boot (`uc24` from `vms/`: `Boot0006 "ubuntu"`, BootCurrent
  0006). So **load needs to make no NVRAM entry**: shim's fallback makes
  it from the restored seed. The two shims are one binary
  (`current_trusted_recovery_boot_assets` in modeenv: `bootx64.efi` and
  `ubuntu:shimx64.efi` have the same hash).
- **The recovery-system API:** `POST /v2/systems` through `snap debug api
  -X POST -H 'Content-Type: application/json' /v2/systems`, as root, with
  `{"action": "create", "label": "bko-20261114", "test-system": true,
  "mark-default": false}`. It answers `202 Accepted`, `"type": "async"`,
  a change ("Create new recovery system with label ..."). With
  `test-system`, snapd reboots into **recover mode** of the new system
  (`snapd_recovery_mode=recover snapd_recovery_system=bko-20261114`) and
  back to run mode; under FDE both boots were unattended. After it,
  modeenv has `current_recovery_systems=20240603,bko-20261114` and
  `good_recovery_systems` the same, and the seed's grubenv
  `snapd_good_recovery_systems=20240603,bko-20261114`. `snap recovery`
  lists it with no notes (not the default). No validation set was needed,
  and nothing was downloaded: it seeds the installed revisions.
- **cmdline-append is refused by the stock gadget:** `"bko.test=1" is not
  allowed in the kernel command line by the gadget` (the pc gadget has no
  `kernel-cmdline` allow list, and the grade is signed). **It isn't
  needed:** the gadget's own command line has the serial console. Seed:
  `console=ttyS0 console=tty1 panic=-1`; run mode:
  `console=ttyS0,115200n8 console=tty1 panic=-1`. The kernel and the
  journal reach ttyS0, but **`/dev/console` is tty1** (the last
  `console=`), so the initramfs's password prompt is on the screen, not
  on serial (below).
- **cloud-init under signed grade: the CIDATA drive works.** cloud-init
  24.1.3: `Datasource DataSourceNoCloud [seed=/dev/vdb][dsmode=net]`.
  After the first boot snapd writes `/etc/cloud/cloud-init.disabled`
  (`disabled-by-marker-file`), so a restored guest never runs cloud-init
  again. The fallback (path 2) and the user's signing key are not needed.
- **console-conf is not skipped:** `console-conf@tty1` and
  `serial-console-conf@ttyS0` run at every boot ("Press enter to
  configure.") in place of the gettys, also once tester exists. They
  wait without blocking anything: boot and SSH go on. So the serial line
  has no login prompt; nothing in the tests types at it.
- **Room** (df in the guest, after the build): seed 882 MiB used of
  1181 (two recovery systems and the pool: 299 MiB free, so a third
  system may not fit), boot 121 MiB, save 57 KiB, data 1.04 GiB
  (`uc24-fde` 1.05). qcow2 2.2 GB (`uc24`) and 2.3 GB (`uc24-fde`)
  allocated after `fstrim -av`, 8 GiB virtual. The tpm dir is 3.9 KB
  (`tpm2-00.permall` and `.lock`), the vars 528 KiB.
- **vm-store and the TPM dir** (read in `vmstore.py`, not run):
  `sidecar_files()` walks subdirectories, so `tpm/tpm2-00.permall` goes
  in the store as a sidecar (`*.lock` is skipped). Its objects are 0400,
  so a linked guest's TPM is read-only: every boot must use a copy, as
  the check boots do.

**What this changes in the design:**

- **The new-machine promise** (Decision 2, cell d) can't be "the
  recovery key once, then unattended". On a new TPM, or on the same TPM
  with other Secure Boot variables, a restored `uc24-fde` reaches only
  **recover mode, with the recovery key typed on tty1**: the data is
  readable, but run mode never boots. Ubuntu Core 24 has no
  re-provisioning that we found. **The user chose the recover-mode limit
  (Decision 2b, 2026-11-14)**; the promises and cell d above say it.
- **Every boot of `uc24-fde` needs copies of its `tpm/` and
  `uefi-vars.fd`.** `vm-run-options` holds only `--uefi --memory 2048`:
  its paths would not resolve, and a boot that reseals writes the TPM's
  NV counter, which a thrown-away disk would no longer match. Without its
  own TPM it can't unlock (a new one boot-loops; no TPM at all is
  untested), so `rebuild-one.sh`'s boot check and compare.py fail on
  `uc24-fde` until U4 teaches them the copies.
- **load makes no NVRAM entry**: shim's fallback in the refreshed gadget
  makes `ubuntu` from the restored seed on the first boot.
- **The sealed keys are files** (no LUKS2 tokens): keeping the header
  (7 MiB a volume) and the files on boot, seed and save keeps the
  same-machine path; nothing in the header names the TPM.
- **The converter is right**, and opening a volume needs ~773 MiB free
  in the rescue image (keyslot 1's argon2i).
- **The marks** are `/run/mnt/data/bko-mark` and
  `/run/mnt/ubuntu-save/bko-mark`; `snap recovery` runs as root.
- **The seed's vfat volume ID and two PARTUUIDs come from the image**,
  the same on every install: a restore keeps them as the rest.
- **The seed has 299 MiB free** with two recovery systems: a third may
  not fit, so the guests keep two.

## What U2/U3 found

2026-11-14, on Copland (copies of both guests, under `cmp-run.sh --reserve
3 3` in /mnt/bko-ram; results in Copland's and Chopin's
`/bkoscratch/u3-ubuntu-core/`, not in the repo), with a static bko-holt
built from this tree.

- **The dump runs in the running system**, as every Linux dump does
  (HoltTest1, BackShot1): the static bko-holt as root in `/root`, in run
  mode, where snap-bootstrap has unlocked and mounted all four partitions.
  core24 has every tool it runs (`cryptsetup`, `unsquashfs`, `blkid`,
  `sfdisk`, `lsblk`). So dumping needs no recovery key, and no
  rescue-system dump of Ubuntu Core is built: the spec's "dumping needs
  the key" and "FDE without the volume opened" apply to that offline dump
  only. dump still refuses a locked ubuntu-data (recover mode of a system
  that didn't unlock it), naming `recovery-key-file`; load needs the key
  (`--key-file ubuntu-data-enc=FILE --key-file ubuntu-save-enc=FILE`; the
  LUKS label names the volume, since snap-bootstrap's dm names change each
  boot).
- **The real dumps** (uc24, uc24-fde booted on copies of their TPM and
  variables): exit 0, no warnings, no refusals. `boot.snapcore`: the four
  partitions with names, PARTUUIDs, types and sizes; modeenv (both recovery
  systems, `pc-kernel_3908.snap`, `core24_2307.snap`, `pc_196.snap`); the
  model (canonical, signed, no storage-safety, gadget pc, core24); os-release
  from the base snap by `unsquashfs -cat`; the seed's two systems with their
  kernels (20240603 on `pc-kernel_1803.snap`, bko-20261114 on
  `pc-kernel_3908.snap`); every snap and revision from state.json (snapd
  2.78 keeps the sequence as `{"revisions": [{"snap": ...}]}`) and the
  `.snap` files alike, hello 42 and 29. backup_sources: the four roots,
  `from` the run-mode paths, mount hints by filesystem UUID.
- **The LUKS2 header backup is 6.5 MiB** (6815744 bytes: two 2 MiB
  metadata areas and the 2.5 MiB keyslots area; the data segment starts at
  7 MiB). Its SHA-256 equals that of the device's first 6815744 bytes, for
  both volumes. Both headers have **no tokens**: keyslot 0 (preferred, the
  TPM key) and 1 (the recovery key).
- **The converter opens the dumped headers**: `bko-holt recovery-key-file`
  from the host's key file, then `cryptsetup open --test-passphrase
  --key-file` on each header file: `Key slot 1 unlocked`. A malformed key
  is refused without echoing it.
- **The run-mode kernel is a symlink, not a grubenv variable**: UC24's run
  grubenv has no `snap_kernel`; `EFI/ubuntu/kernel.efi` on ubuntu-boot links
  to `pc-kernel_N.snap/kernel.efi`. The check script follows the link (and
  still checks `snap_kernel` when a grubenv has one).
- **UC24's pc gadget is UEFI only**: the BIOS Boot partition is all zeros,
  and the MBR's 440 bytes are a stub that prints "Please boot in EFI mode."
  The spec's "BIOS through the gadget's core.img" doesn't exist (Decision
  4's `uc24-bios` is moot). load writes the MBR stub back as it was; an
  empty BIOS Boot writes nothing; the plan notes UEFI only.
- **The running /etc/fstab** is the base snap's binds and tmpfs (`rootfs /`,
  `/home`, `/var/lib/snapd`, ...), none of them the four partitions: load
  doesn't read fstab on Ubuntu Core (otherwise 28 "not on the layout's
  disks" notes).
- **Why `bko-20261114` didn't ask for the key** (`recdbg.sh`: recover mode
  on a new swtpm and fresh variables, the system's grubenv given
  `systemd.journald.forward_to_console=1 snapd.debug=1` and ttyS0 as the
  last console, so snap-bootstrap's log reaches the serial log; no key
  typed). Recover mode runs the recovery system's own kernel, and its
  initramfs's snap-bootstrap:
  - `bko-20261114` (pc-kernel 3908, snapd 2.7x's snap-bootstrap):
    `Error with keyslot "external:legacy"` and `"external:legacy-fallback"`
    (TPM_RC_INTEGRITY), then `cannot unlock encrypted ubuntu-data ...:
    no valid keyslots and / or no more passphrase, PIN or recovery key
    tries remaining`, the same for ubuntu-save, and it boots on with both
    locked. No prompt is ever issued.
  - `20240603` (pc-kernel 1803, snapd 2.63's): `Please enter the recovery
    key for disk /dev/disk/by-partuuid/...` for ubuntu-data, then
    ubuntu-save.

  snapd's source (`cmd/snap-bootstrap/cmd_initramfs_mounts.go`, 2.78 and
  master) does allow the recovery key in recover mode (`AllowRecoveryKey:
  true` for ubuntu-data and ubuntu-save's fallback). The newer secboot it
  uses (`ActivateContext`, `WithRecoveryKeyTries(3)`) tries a recovery key
  only against keyslots its LUKS2 tokens type as recovery keyslots; the
  headers snapd 2.63 made at install have no tokens, so there is no
  recovery keyslot for it and no prompt (an inference from the source and
  the journal; the secboot backend that types keyslots wasn't read).
  snapd 2.63's snap-bootstrap tries the recovery key against every
  keyslot.

  **So `--new-machine` names the first good recovery system**, the one the
  device was installed from: its kernel made the keyslots and opens them.
  For uc24-fde that is `20240603`. A device installed by a newer snapd
  (tokens in its headers) would be served by any of its systems; one whose
  install system was removed, with a tokenless header, would have none
  that asks (not seen; a risk below).
- **The plans** (a blank 8 GiB nbd target on Chopin): uc24 and uc24-fde
  plan with no problems: the gadget's table exact (BIOS Boot, ubuntu-seed
  with the ESP type, boot, save, data; every PARTUUID and name), `mkfs.fat
  -i ABA0ACE2 -n ubuntu-seed`, every ext4 with its UUID and label,
  `luksHeaderRestore` and `cryptsetup open --key-file` for both volumes (no
  luksFormat), the four mounts under `/mnt/bko-target/run/mnt`, and a boot
  stage of the check, `snapcore-boot-areas`, the log and the tail; no
  grub-install, efibootmgr, update-grub or chroot. `--new-machine` adds
  `grub-editenv .../ubuntu-seed/EFI/ubuntu/grubenv set
  snapd_recovery_mode=recover snapd_recovery_system=20240603`. Without the
  key files: two problems naming `recovery-key-file`. A 9 GiB target with
  `--fit-largest 30`: only ubuntu-data grows (by 1 GiB). No
  recovery-key-shaped string in any dump or plan.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| U1 | Fetch the image with the user's OK, verify SHA256SUMS.gpg, then `make-ubuntu-core.sh` by Decision 3's path, both guests on Chopin, copied to the second host (the same sha256), with `rebuild-one.sh` lines. Settle every *check*: the labels, where the keys live, the recovery-key bytes as a LUKS key, re-provisioning onto a **different** swtpm, cloud-init under signed grade, the console-conf skip, `cmdline-append`, the NVRAM entry, the recovery-system API, os-release, room. Write "What U1 found" here. Done when both guests boot alone, SSH works, and `uc24-fde` boots unattended twice. **Fetched 2026-11-14** on Chopin, in `/bkoscratch/rebuild-inputs/ubuntu-core24/`: `ubuntu-core-24-amd64.img.xz` (470,060,012 bytes, SHA-256 `d82b5b9b...71622a0`), `SHA256SUMS` with a good `SHA256SUMS.gpg` signature from key `843938DF228D22F7B3742BC0D94AA3F0EFE21092` (Ubuntu CD Image Automatic Signing Key (2012), kept as `cdimage-keyring.gpg`), and the model assertion (canonical, grade signed, no storage-safety, timestamp 2024-04-19). The image is still the 2024-06-03 build. **Built 2026-11-14 on Chopin** ("What U1 found"): `uc24` and `uc24-fde` in `~/backotter-work/vms/`, each booted alone to SSH, `uc24-fde` unattended twice; `make-ubuntu-core.sh`, `testvm/ubuntu-core/`, the `rebuild-one.sh` lines; every *check* settled: on a new TPM run mode boot-loops with no prompt, recover mode of the image's system takes the key and unlocks the data, and run mode never comes back (no re-provisioning; the user chose the recover-mode limit, Decision 2b); fresh firmware variables break the same TPM too (dbx). **Copied to Cinqcent** 2026-11-14 (guests, `tpm/`, vars, rebuild inputs and the 0600 key file, the same sha256), and `uc24-fde` booted unattended twice there on copies of its own TPM and variables. **Done** | 1.5 d |
| U2 | dump (High subagent): `osrel` row; `snapcore.rs` (detection, modeenv, model, seed, revisions); `boot.snapcore`; `backup_sources` with mount hints; LUKS2 header capture; `recovery-key-file`; `redact.rs` labels and the key rule; scenario `snapcore_tree(fde)`. **Built 2026-11-14** (uncommitted): `osrel.rs` row `("ubuntu-core", "24", "Ubuntu Core 24")` appended (the names list ends "Sculpt OS or Ubuntu Core 24") and test `ubuntu_core` (20, 22 and 26 refused); `holt/src/snapcore.rs` (`ROLES`, `Env` for modeenv and grubenv, `parse_model`, `state_snaps` (both sequence forms), `changes_in_progress`, `find`, `read`, `add_to_boot`, `refusal`, `recovery_key_bytes` and `recovery_key_main`); `holt/src/luksheader.rs` (generic: `capture` by `cryptsetup luksHeaderBackup` into `--luks-header-dir`, default `/run/bko-holt/luks-headers`, `storage.luks_headers` with file, bytes and SHA-256; `kept`, `path_at_load`, `check_file`); `report.rs` `build_with` (the LUKS options), backup_sources replaced on Ubuntu Core, `luks_headers` only when non-empty, `boot.snapcore`, `refusal` first; `lib.rs` `dump_with`; `main.rs` `dump --keep-luks-header DEV --luks-header-dir DIR`, `recovery-key-file OUT` (stdin, no echo on a terminal, 0600, prints a 12-digit SHA-256 prefix), the dump's output scrubbed; `redact.rs` GENERIC_LABELS 85 (+the six `ubuntu-*`), `scrub_recovery_keys` in every redacted text, and test; the v1 refusals at dump (not run mode, `kernel_status` try/trying, changes in progress, another gadget, architecture or base, an encrypted role not open, a role not at its mount point, two Ubuntu Core disks). Scenarios `snapcore_tree(fde)` with `SnapcoreRunner` (writes the header files), tests `snapcore_guest`, `snapcore_fde_guest`, `snapcore_refusals`; unit tests in snapcore.rs (6) and luksheader.rs (2). The real dumps: "What U2/U3 found" | 1 d |
| U3 | load (High): the gadget's partitions exact; `keep_header` (generic, with `--keep-luks-header`); `snapcore_boot` (check script, BIOS Boot, no NVRAM entry, log); `--new-machine` (Decision 2b: the restored seed `grubenv` set to recover mode for a system that asks for the key; *check* why `bko-20261114` didn't ask); v1 refusals; tests. **Built 2026-11-14** (uncommitted): `holt/src/load/snapcore.rs` (`of`, `MARKER`, `recover_system`, `check_script`, `Builder::snapcore_mounts`, `snapcore_problems`, `snapcore_boot`, `boot_areas_main` and `boot_area_writes` for the hidden `bko-holt snapcore-boot-areas LAYOUT DISK DEV`); `commands.rs` `luks_keep_header` (generic: luksHeaderRestore --batch-mode, open with `--key-file` by the container's name, its LUKS label or the device; the header file's SHA-256 checked at plan; the data segment must fit; LUKS1 and `--new-uuids` refused; `restored_key_files` adds nothing to a kept header), the mount stage's snapcore mounts with no fstab and no `/`, the boot stage's early return after ABRoot's, `--new-machine` refused on other layouts; `load/mod.rs` `--new-machine`, `--luks-header-dir`; `restored_marker()` and status.rs take `run/mnt/data/system-data/var/lib/snapd/modeenv`; `plan.rs`'s output scrubbed. The table is the generic sfdisk one (names, type GUIDs, PARTUUIDs, attributes, starts and sizes exact); a plan whose seed, boot or save differs in size from boot.snapcore's is refused, so only ubuntu-data changes under `--fit-largest`. No NVRAM entry, no grub-install; the seed's vfat ID kept by `mkfs.fat -i`. `--new-machine` writes recover mode of the first good recovery system (the open question, settled in "What U2/U3 found"). Tests: `check_passes_and_fails` (a real tree, five mismatches, a refresh warned), `recover_system_choice`, `boot_areas` (written to a file; an empty BIOS Boot), and the scenarios above. Warm build on Chopin (`/bkoscratch/holt-warm/ubuntu-core`): 566 lib tests pass; every other target passes but golden_plans, whose only failure is the known `ubuntu24-luks`/`ubuntu24-uefi` "no corpus dump" (no corpus plan changed); clippy has nothing new in these files. **Review fixes the same day** (a High review, no must-fix): an encrypted role whose LUKS2 header dump couldn't keep is a dump refusal (`snapcore::Dump::require_headers`, so `dump` exits 3), and dump warns that the header files (default `/run/bko-holt/luks-headers`, RAM) must be kept with the dump, which the plan's notes say too; the atimes on Ubuntu Core are listed and put back under `T/run/mnt` (`atimes_cmd_in`, `Builder::atimes`: the rest of `T/run` still pruned; restore and boot stages), since the whole tree is there; the same-machine note and the promise above say "unattended only if nothing was resealed since the backup", and cell f records the case; the kept header's fit check takes a fixed segment whole (`have < offset + size`) and a `dynamic` one by its offset only; `recovery-key-file` refuses an OUT that is or holds a recovery key (without echoing it), and reads the key through `sh` with a trap that restores the terminal's echo on every exit, Ctrl-C too; `dump --keep-luks-header`/`--luks-header-dir` with `--disk` is refused. Tests added: `atimes_cover_run_mnt`, `recovery_key_file_refuses_a_key_as_its_name`, and in the scenarios the refusal of an unkept header, the header warning and note, a fixed segment too big, the atimes lines. Suite: 568 lib tests pass, golden_plans only the known miss; both real dumps re-planned (bko-holt `e0490bcf3127`): the same plans, the new notes, the atimes under `run/mnt` | 1.1 d |
| U4 | Guest tools: vm-mount's snapcore mode (with FDE), vm-update's snap branch, fingerprint/pkgset, `K-snapcore`, compare.py's four-root tree; tests | 0.75 d |
| U5 | SticTest1 `uc24`, both archivers, two hosts, 0 FAIL | 0.4 d |
| U6 | HoltTest1 1U: cells a-f in ht1.sh (the TPM copy or new TPM, the recover-mode boot, the prompt typed by `console-boot.py`, the post-restore refresh), judge.py rules, `ram-sizes.tsv`; runs on Chopin and a second host. **judge.py's fs rules done 2026-11-18** (what `uc24`'s HoltTest1/HoltTest2 needs): an Ubuntu Core source (its record has `run/mnt/data/system-data/var/lib/snapd/modeenv`, load's `snapcore::MARKER`) is judged by `SNAPCORE_STAGE`, not `BOOT_STAGE`: it excuses only load's log, `run/mnt/data/system-data/var/log/bko-holt/`, and that `var/log`'s directory times, so everything on ubuntu-seed and ubuntu-boot, every `.snap`, `modeenv` and `state.json` is held to the source's bytes (the summary line counts them); the source's record must have `modeenv`, `state.json`, both grubenvs, ubuntu-boot's grub.cfg, the seed's `bootx64.efi`, a `.snap` in the seed's and snapd's `snaps/` and a `kernel.efi` on ubuntu-boot (`SNAPCORE_REQUIRED`, `SNAPCORE_GLOBS`). judge-test.py `FsSnapcore` (5 cases). By-hand run `uc24close` on Cinqcent (FAIL only on those three paths) re-judged offline: PASS, 3 from the boot stage, 72 paths held. Not done: cells b-f (and with them any rule for `--new-machine`'s seed grubenv, cell d), the boot-churn list (U7), `ram-sizes.tsv` | 1 d |
| U7 | Golden plans and the corpus test; Test2 registration in the batched pass | 0.4 d |
| U8 | Docs: holt/README.md (list, the three FDE promises), layload.md (`keep_header`, the snapcore boot stage), HoltTest1's 1U row, SticTest1's README, the rescue image's docs for `recovery-key-file`, memory | 0.2 d |
| U9 | *Optional* (Decision 6): BackShot1 on the Thinkpad with Ubuntu Core and its real TPM | 1 d + the user's reinstall |
| | *≈ 6.25 d without U9; +0.75 d on Decision 3's path 2; +0.4 d with `uc24-bios`* | |

U2 and U3 can start beside U1 from these facts. Their golden plans (U7)
wait for U1's dumps. Each piece is committed on main once its tests pass
and the user says to commit. Nothing is pushed until the user says so.

**Costs** (est.): U1 needs ~1 h on Chopin (the refresh pulls ~1 GiB of
snaps per guest) and `--reserve 10 2` in /mnt/bko-ram, moved to disk
when done. Each guest is ~3 GiB a host, plus the 0.45 GB image. U5 and
U6 are about 1 h of queue a host. The only time it asks of the user is
the OKs.

## Risks

- **A new machine reaches recover mode only** (settled in U1, accepted by
  the user as Decision 2b). Ubuntu Core 24 doesn't re-provision onto a
  different TPM, and bko doesn't take over sealing (option C). A newer
  snapd or UC26 may add re-provisioning: a later run of cell d would show
  it, and the promise would then grow.
- **A dbx update ends unattended boots** on the same machine too: PCR 7
  measures dbx. That is Ubuntu Core's behaviour with or without bko,
  and snapd reseals for the dbx updates it applies itself.
- **snapd moves under us.** Newer snapd moves keys from files to LUKS2
  tokens and adds named keyslots and new FDE APIs. Keeping the whole
  header is robust to that, but the "recovery key as 16 bytes" converter
  must match the snapd that made the slot. U1 tests it on the refreshed
  guest, and the converter's tests pin the format.
- **Signed grade and cloud-init.** If the stock image won't take a
  CIDATA drive, path 2 needs the user's Ubuntu One signing key (their
  step) and gives a dangerous-grade guest.
- **The stale stock image.** A rebuild refreshes to whatever the store
  has that day, so two builds differ. The build records `snap list
  --all`, and HoltTest1 compares a restore with its own source, never
  with a rebuild.
- **The secret on the hosts.** `uc24-fde.recovery` lives in rebuild-inputs
  on each host (0600). The leak scan and the redaction rule keep it out
  of plans, corpora and bug reports. A careless `set -x` in ht1.sh would
  print it, so the scripts pass it by file only.
- **The same machine means the same firmware variables too.** Restoring
  onto the same box after a firmware update or a dbx update changes
  PCR 7. That is the new-machine case: recover mode only (Decision 2b).
  The docs say so.
- **The swtpm state travels with the guest.** If vm-store can't carry a
  directory, the FDE guest's TPM is lost on a copy, and every copy is a
  "new machine". U1 settles it (a tarball sidecar at worst).

- **A tokenless header and no install system.** `--new-machine` relies on
  the recovery system the device was installed from (U3): a newer
  snap-bootstrap doesn't try the recovery key on a header snapd 2.63 made.
  If that system is removed (`snap recovery` can remove systems), recover
  mode of the rest boots with ubuntu-data locked, and the restored data is
  reachable only offline (cryptsetup and the converted key).

## Out of scope

- UC20 and UC22, arm64, Raspberry Pi gadgets, and OP-TEE FDE.
- Custom gadgets with extra partitions or other sizes (refused in v1,
  later if asked).
- Remodelling, validation sets, and the snap store's device serial
  re-registration on a new machine. The serial's keys come back from
  ubuntu-save as they were.
- Passphrase or PIN authentication added to the FDE keys
  (`snap-tpmctl add-pin`): those are extra keyslots and come back with
  the header, but they aren't tested.
- bko resealing keys itself (option C).
- Ubuntu Desktop's TPM-backed FDE (the hybrid "classic with snaps"
  model). The `keep_header` path may serve it later.

## Decisions for the user

**Approved by the user on 2026-11-14, with every recommended option.**

1. **Which release:** **Ubuntu Core 24** (recommended: as asked, an LTS
   with years left, and its stock image is published with a signed
   SHA256SUMS); or **UC26** instead (the docs call it latest, *check*
   its image); or both (+0.5 d, two more guests and a row).
2. **What an FDE restore promises:** **keep the LUKS2 header (option A):
   unattended on the same machine** (on a new one see 2b)
   (recommended: the only scheme that keeps the TPM-sealed keys working
   without bko sealing anything); or option B, a new header unlocked by
   the recovery key (a new volume key, the recovery key at every boot
   until the user re-provisions), offered as a load flag beside A; or
   A only, with no B flag.

   2b. **(Asked after U1, 2026-11-14, when U1 found no re-provisioning.)
   What an FDE restore promises on a new machine:** **the recover-mode
   limit**: recover mode with the recovery key typed on the screen, the
   data readable, a normal boot only after a reinstall; `load
   --new-machine` makes the first boot recover mode (**chosen by the
   user**); or research first (snapd 2.78's FDE API, UC26, ~0.5 d); or a
   decrypting restore that boots unattended but unencrypted (~1 d,
   unproven).
3. **How the guests are built:** **the stock signed image with a NoCloud
   CIDATA drive** (recommended: Canonical's own image and model, no
   account needed); or our own dangerous-grade model built by
   `ubuntu-image` (needs the user to register a signing key, +0.75 d);
   or console-conf driven over serial with the user's Ubuntu One email.
4. **Guests:** **`uc24` and `uc24-fde`** (recommended: one per restore
   promise); or add `uc24-bios` for the gadget's BIOS Boot path (+0.4 d).
5. **HoltTest1's group:** **1U** (recommended: it is Ubuntu, the guests
   are small and fast, and 1Obscure already holds 16 guests); or
   **1Obscure** beside the other immutable systems.
6. **BackShot1 on the Thinkpad:** **not now** (recommended: the guests
   cover the layout, and swtpm covers sealing); or U9, a reinstall with
   Ubuntu Core, the only test on a real TPM.
7. **Secure Boot on both guests:** **on** (recommended: FDE requires it,
   and `uc24` then boots the same shim chain); or off on `uc24`.
