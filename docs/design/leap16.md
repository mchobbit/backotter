# openSUSE Leap 16 support

2026-11-14. Asked for by the user: support openSUSE Leap 16, as one of eight
distributions added in parallel (Proxmox, Guix, Leap 16, Vanilla OS,
Slackware, Solus, OpenIndiana, Kali), the way AnduinOS, KDE neon,
elementary, PikaOS, BigLinux and Garuda were added (1edb4b5). **Status:
approved 2026-11-14 by the user with every recommended decision (the last
section).**

**Conventions.** est. marks an estimate. *Check* marks a fact the public
sources don't settle, to be settled on the first guest (L1) before the code
that depends on it. Leap's facts come from press coverage of the 16.1
release (Phoronix, LWN, Linuxiac, Linux Journal, The Register, early
November 2026), get.opensuse.org/leap/16.1, the Leap 16.0 release notes on
doc.opensuse.org, the SLES 16.1 release notes, and Agama's documentation
(agama-project.github.io: unattended installation, boot options, AutoYaST
support) and SUSE's "Automated Installation Using Agama" for SLES 16.0, read
2026-11-14. Nothing has been downloaded or installed yet. Code references
are to local `main` at 1edb4b5.

## Summary

- openSUSE Leap 16 becomes a supported system for bko-holt and both
  archivers, with one QEMU guest (`leap16`), a SticTest1 entry, a place in
  HoltTest1 **1Obscure** beside `tumbleweed`, a golden plan and the Test2
  registrations; BackShot1 on the Thinkpad is an optional last step.
- Leap 16 is Tumbleweed's sibling in all that bko-holt touches: zypper and
  RPM, dracut, GRUB 2 in `/boot/grub2`, Btrfs with openSUSE's subvolumes
  under `@` and snapper's root snapshots (the live root
  `@/.snapshots/N/snapshot` as the default subvolume), SELinux enforcing.
  All of that is built and passes on `tumbleweed` today.
- What is new for bko: the **Agama** installer (no YaST, no AutoYaST DVD
  install), so the guest's build is new; a **UEFI + Secure Boot** guest with
  SUSE's shim (the `tumbleweed` guest is BIOS + MBR, so openSUSE's UEFI path
  is untested today); a **versioned** release (`ID=opensuse-leap`,
  `VERSION_ID=16.1`), updated by `zypper up`, not `dup`.
- `testvm/make-opensuse.sh` is reused: the fetch-and-verify step (signed
  `.sha512` for Leap, `.sha256` for Tumbleweed) and its SELinux relabel boot and check boot are the same; the
  install step gains a Leap arm that boots the Agama ISO under OVMF with an
  Agama profile.
- ~2.75 d (est.), plus an optional BackShot1 run.

## What Leap 16 is (as far as bko cares)

| | openSUSE Leap 16.1 | Nearest supported system |
|---|---|---|
| Release | 16.0 out 2025-10-01; **16.1 out about 2026-11-03**, built from SLES 16.1's sources; one minor release a year, each supported until a while after the next (*check* 16.0's end of life) | Tumbleweed (same tools, rolling) |
| os-release | `ID="opensuse-leap"`, `ID_LIKE="suse opensuse"`, `VERSION_ID="16.1"` (*check* the exact strings) | Tumbleweed (`opensuse-tumbleweed`, `VERSION_ID` the snapshot's date) |
| CPU | x86-64-v2 at least (16.0 on); `-cpu host` on the farm qualifies | |
| Packages | zypper and RPM; the OSS, non-OSS and update repositories of `download.opensuse.org/distribution/leap/16.1/` (*check* the repo names Agama writes) | Tumbleweed |
| Init | systemd | Tumbleweed |
| Kernel | `kernel-default` 6.12 (SLES 16's; *check* the 16.1 version), several kept by zypper's `multiversion.kernels` | Tumbleweed |
| Initramfs | dracut, `/boot/initrd-K` (*check*) | Tumbleweed |
| Boot loader | GRUB 2 (`/boot/grub2`), with `grub2-snapper-plugin`'s snapshot menu; on UEFI SUSE's Microsoft-signed shim and SUSE-signed GRUB in `EFI/opensuse` (shim.efi, grub.efi, MokManager.efi, grub.cfg, boot.csv) and the fallback `EFI/boot/bootx64.efi` + `fallback.efi` (*check* the names). SLES 16.1 has systemd-boot as a technology preview; whether Leap 16.1's Agama offers systemd-boot or grub2-bls, and that GRUB stays the default, is a *check* | Tumbleweed (BIOS GRUB), Fedora (GRUB through shim, kept from the ESP) |
| Firmware | UEFI and BIOS on x86_64 | |
| Install | **Agama** (web UI over a live system), unattended by `inst.auto=URL` pointing at a JSON or Jsonnet profile; Agama also reads a subset of AutoYaST XML. `inst.finish` sets what happens at the end (values *check*; the docs list none); `inst.install=0` stops after reading the profile. Agama asks to retry when it can't fetch or parse the profile instead of going on. Profile scripts: `pre`, `post` (in the chroot or not), `init` (first boot); a 16.0 bug put init scripts under the wrong path (*check* on 16.1) | Tumbleweed's AutoYaST (YaST) |
| Images | an offline and an online installer ISO, each with a `.sha512` and a signature by the openSUSE Project Signing Key (`Leap-16.1-offline-installer-x86_64.install.iso` or similar; *check* names, path and size) | Tumbleweed's DVD |
| Layout (Agama's default, *check*) | GPT; on UEFI a FAT ESP at `/boot/efi`, on BIOS a BIOS boot partition; swap; one Btrfs `/` with openSUSE's subvolumes (`@/home`, `@/opt`, `@/root`, `@/srv`, `@/usr/local`, `@/var` with `C`, `@/boot/grub2/x86_64-efi`, `@/boot/grub2/i386-pc`) and snapper's `@/.snapshots`, the root being `@/.snapshots/1/snapshot`. Leap 15 put `/home` on a separate XFS on big disks; whether Agama does for Leap 16 is the *check* | Tumbleweed (the same subvolumes, its `/boot` on ext4) |
| Snapshots | snapper: the installer's first snapshot, then zypper's pre/post pairs (snapper-zypp-plugin) and the timeline; `snapper rollback` changes the default subvolume | Tumbleweed |
| SELinux | the default for new installs (16.0 on), `targeted`, enforcing; AppArmor no longer the default. Whether Agama leaves a first-boot relabel pending, as Tumbleweed's AutoYaST install does, is a *check* | Tumbleweed (enforcing, relabel at first boot), Fedora, Rocky |
| Immutable mode | **new in 16.1**: Agama offers it in place of Leap Micro: read-only root, transactional-update. Out of scope here (Decision 4) | Kinoite/Bazzite are another kind (ostree) |
| Network | NetworkManager (wicked is gone) | Tumbleweed |

## What already works, and what doesn't

**bko-holt `dump`:**

- `osrel.rs` `SUPPORTED` refuses it: no `opensuse-leap` row. It needs
  `("opensuse-leap", "16", "openSUSE Leap 16")` (`supported()` matches the
  major, so 16.0 and 16.1 both pass), with a unit test from the guest's
  os-release and a refusal of Leap 15.x (Decision 2).
- `redact.rs` knows `opensuse` already. Packages, the initramfs generator
  (dracut), GRUB (grub2 names), Btrfs subvolumes, snapper's slots and the
  default subvolume, SELinux labels: as Tumbleweed's. `detect_loader` should
  find GRUB through shim from BootCurrent (`\EFI\opensuse\shim.efi`) and set
  `via_shim`; *check* on L3's dump (with a fresh OVMF store the guest may
  boot the fallback path, as `anduinos2` did, and then shim shows only from
  the ESP).

**bko-holt `load`:**

- **UEFI GRUB:** `grub_dir() == "/boot/grub2"` sends it to the Fedora arm
  (commands.rs, `("grub", true) if self.grub_dir() == "/boot/grub2"`): no
  `grub2-install`, the ESP's shim, signed GRUB and MokManager come back with
  the ESP, grub.cfg is remade with `grub2-mkconfig -o /boot/grub2/grub.cfg`
  (os-prober off), then `efi_entries()`. That is right for Leap, which also
  must not run `grub2-install`/`shim-install` over the signed files. Two
  small changes: the arm's note says "on Fedora"; it should name the system
  (or say "GRUB 2 through shim"), and a unit test pins Leap through it.
  openSUSE's own `update-bootloader --refresh` (perl-Bootloader) would do
  the same as `grub2-mkconfig` here; `grub2-mkconfig` is kept (Decision 3).
- **The ESP's `grub.cfg` stub** finds `/boot/grub2` by the Btrfs UUID, which
  `format` keeps, with openSUSE's `btrfs_relative_path` (paths inside the
  default subvolume): it relies on load setting the default subvolume to
  the live root snapshot, which it does for Tumbleweed (`btrfs subvolume
  set-default`). *Check* the stub's text on L1.
- **BIOS:** Tumbleweed's arm, unchanged (no guest; a unit test).
- **SELinux:** the restore keeps `security.selinux`; the boot stage's
  `setfiles` step relabels what it wrote (`/boot`, `/var/log/bko-holt`),
  as for Tumbleweed and Fedora. No relabel trigger is left on the target.
- **Snapshots:** snapper's chain, the old root and the rollback default
  restore as on Tumbleweed (docs/design/btrfs-snapshots.md, `--clone-from`).
- **systemd-boot or grub2-bls** (only if L1 finds Agama defaults to one of
  them, against expectations): the `("systemd-boot", true)` shim arm (for
  sdbootutil) exists; grub2-bls would be a new arm. Not planned.

**The rescue image:** no change ([[one-kernel-rescue]]): btrfs-progs,
dosfstools, dracut in the chroot.

## Guest

One guest, `leap16`: Leap 16.1, **UEFI with Secure Boot on** (OVMF's
Microsoft-keyed store; SUSE's shim is signed by Microsoft's UEFI CA, so no
MOK is needed), Agama's default Btrfs-with-snapshots layout, SELinux
enforcing, no desktop (Decision 1). UEFI because `tumbleweed` already covers
openSUSE on BIOS; Leap's BIOS path is the same arm and gets a unit test.

**How it is built:** `make-opensuse.sh` gains a Leap mode,
`make-opensuse.sh leap 16.1 WORK_DIR`, beside the existing
`make-opensuse.sh SNAPSHOT WORK_DIR` (unchanged for Tumbleweed and
`rebuild-one.sh`'s `tumbleweed)` line):

1. **Fetch and verify** as now, from
   `download.opensuse.org/distribution/leap/16.1/offline/` (*check*): the
   offline ISO (everything on it, so a rebuild needs nothing that expires),
   its `.sha512` and `.sha512.asc`, checked with `gpgv` against the same
   openSUSE Project Signing Key (`AD485664...29B700A4`, taken from the ISO
   as now; *check* Leap's ISO carries it). Kept in WORK_DIR and in
   `/bkoscratch/rebuild-inputs/leap16/` on both hosts.
2. **Install:** QEMU under OVMF (Secure Boot build, Microsoft-keyed vars)
   boots the ISO's kernel and initrd (*check* their paths; Agama's ISO is a
   KIWI live image) with `console=ttyS0,115200n8 inst.auto=http://10.0.2.2:PORT/agama.json`
   and `inst.finish=` whatever powers off (*check*), the ISO as a CD-ROM,
   `-no-reboot`. The profile, `testvm/opensuse/agama.json` (placeholders
   filled by sed, as autoinst.xml's, and checked with `python3 -m json.tool`
   and, if the ISO has it, `agama profile validate`):
   - `product`: Leap 16.1's ID (`Leap_16.1`? *check*), immutable mode off;
   - `storage`: the default proposal on the 24 GiB disk (Btrfs with
     snapshots, the ESP, swap), nothing custom, so the guest is what a user
     gets;
   - `software`: the minimal server patterns plus `openssh`, `snapper`,
     `grub2-snapper-plugin` (*check* which are already in), no desktop;
   - `root`: the hashed password and the SSH key (as now, made by the
     script); `user` none;
   - `localization`: en_US.UTF-8, America/New_York, `us`;
   - `scripts.post` (in the chroot): enable `sshd`, open SSH in firewalld if
     it is on, the serial console in `/etc/default/grub` and
     `grub2-mkconfig`, `multi-user.target`, then echo
     `BACKOTTER-INSTALL-OK` to `/dev/ttyS0`, which the script waits for (as
     now). Agama's own logs (`agama logs store`) go out on a second virtio
     disk if the install fails.
   `answer-popups.py` isn't needed (Agama has no pop-ups when unattended;
   it asks only when the profile fails, which the script treats as a
   failure on timeout).
3. **The relabel boot and the check boot**, unchanged, but booted under
   OVMF with Secure Boot (the same `boot_guest` with the guest's vm-run
   options). The check boot also records `mokutil --sb-state` and requires
   `SecureBoot enabled`. If Agama leaves no relabel pending, the relabel
   boot is simply a first boot (its findings say so).
4. `qemu-img snapshot -c fresh-install`, as now.

The work goes in the existing script, not a copy: the fetch, the SSH key,
the boot and check helpers are shared, and only `GUEST_NAME`, the URL, the
ISO name and the install step differ by mode. The SELinux relabel change
that another session made to `make-opensuse.sh` is already in 1edb4b5, so
this builds on it.

Each piece as for the others: `rebuild-one.sh` (`leap16)` line, the ISO
check), `DISK_SIZE=24G` (Agama's snapshot proposal wants more than
Tumbleweed's 16 GiB; *check* its minimum), `vm-run-options`
`--uefi --secure-boot` (*check* vm-run's spelling), `holttest1d/ram-sizes.tsv`
(est. D 7, MEM 4 until measured), both hosts by `vm-store` publish, a
GuestTest1 grade.

**vm-update:** `in-guest.sh`'s `Linux.zypper` section runs `zypper dup`,
right for Tumbleweed only. A Leap arm (`ID=opensuse-leap`): `zypper -n
--gpg-auto-import-keys refresh`, then `zypper -n up --auto-agree-with-licenses
--no-recommends` (103 means run again, as now), then `zypper clean -a`;
Tumbleweed's "unfreezing from openSUSE's history" step is skipped (Leap's
repositories aren't snapshots). snapper-zypp-plugin makes a pre/post pair
per update: the image keeps them, as `tumbleweed`'s does today (*check* how
many a `--try` adds; if they grow, cleanup with `snapper cleanup number`
before shutdown, Decision 5). `fingerprint.py` and the `vm-update` header
name Leap. GuestTest2: tumbleweed's rules (`S-zyp`, `L-grub`, the kernel
and boot rules) and `known-lines.json#zypper`; Leap's repository lines may
need adding.

## What L1 found (2026-11-14)

The guest built on the first try on Cinqcent, in 307 s, and is in
`vms/leap16` on Cinqcent and Chopin (`leap16.qcow2` SHA-256 `a1595e51...`,
the same on both; `image-state` comes with the first real update), and
boots on Chopin too (`vm-update --probe`, SSH up).  It
settles these *checks*:

- **The ISO:** `Leap-16.1-offline-installer-x86_64-Build60.2.install.iso`,
  4,516,216,832 bytes, in `download.opensuse.org/distribution/leap/16.1/offline/`
  with a `.sha512` (not `.sha256`) and `.sha512.asc`, signed by the openSUSE
  Project Signing Key; the key is on the ISO at
  `/install/gpg-pubkey-29b700a4-*.asc`, not its root.  Kept in
  `/bkoscratch/rebuild-inputs/leap16` on both hosts.  The build pins Build60.2.
- **os-release:** `ID="opensuse-leap"`, `ID_LIKE="suse opensuse"`,
  `VERSION_ID="16.1"`, as assumed.  Kernel `kernel-default` 6.12.0-160100.8
  (160100.9 after an update); shim 16.1, grub2-x86_64-efi 2.14, snapper
  0.12.1, selinux-policy-targeted 20260826, dracut 059.
- **The install:** not `-kernel` (QEMU's direct boot would put the
  SUSE-signed kernel past Secure Boot's check): `vm-run --uefi` boots the
  ISO's own shim and GRUB, whose default entry starts after 60 s, and Agama
  finds `autoinst.json` on a second CD labelled `OEMDRV`.  `product.id` is
  `openSUSE_Leap`, mode `standard`.  Agama's own reboot at the end ends QEMU
  (`-no-reboot`); no `inst.finish` is needed.  The live system has no serial
  console, so `leap-post-live.sh` writes the facts to `/dev/ttyS0` itself.
- **The layout:** GPT, a 512 MiB ESP at `/boot/efi`, one Btrfs `/`
  (21.5 GiB), a 2 GiB swap partition, no separate `/home`.  Subvolumes `@`,
  `@/boot/grub2/x86_64-efi`, `@/boot/grub2/i386-pc`, `@/var`, `@/usr/local`,
  `@/srv`, `@/root`, `@/opt`, `@/home`, `@/.snapshots`; the default (and the
  booted root) is `@/.snapshots/1/snapshot`, mounted with no `subvol=`, as
  Tumbleweed's is.  snapper's `root` config, one snapshot.
- **The boot loader:** GRUB 2 through shim; the ESP holds `EFI/opensuse/`
  (shim.efi, grub.efi, grubx64.efi, MokManager.efi, grub.cfg, boot.csv) and
  `EFI/boot/` (bootx64.efi, fallback.efi, MokManager.efi).  Agama writes an
  `opensuse-secureboot` entry for `\EFI\opensuse\shim.efi`; with vm-run's
  fresh vars each boot, shim's fallback makes it again (`boot-current=0001`),
  so the guest needs no `uefi-vars.fd`.  `GRUB_TERMINAL="gfxterm"`: GRUB's
  menu stays off the serial console, the kernel's (`extraKernelParams`) is
  on it.  No systemd-boot or grub2-bls offered by the default profile.
- **SELinux:** enforcing, targeted; Agama leaves
  `/etc/selinux/.autorelabel`, so the relabel boot runs, as Tumbleweed's.
  After it, one file is mislabelled, `/var/log/plymouth-debug.log`
  (written during that boot), which the check boot reports and allows.
- **Secure Boot:** `mokutil --sb-state` says enabled on the check boot.
- **firewalld:** on; the post script's `--add-service=ssh` keeps SSH open.
- **vm-update --try:** ok on Cinqcent, 137 s: `zypper up` changed 3
  packages, a new kernel booted, the host mounts its 11 file systems, and
  in-guest.sh dropped the install DVD's repository (`Installation.repo`,
  which Agama leaves).
- **Usage:** `make-opensuse.sh leap 16.1 WORK_DIR` (the spec said `leap16`);
  `rebuild-one.sh leap16` runs it.

## Tests

**SticTest1** (`testvm/stictest1/leap16`): from `tumbleweed`'s (Btrfs
subvolumes, snapper's read-only snapshots, `bkoshim.py` for setfattr if the
guest has no attr package) with `chimera-uefi`'s `fatattr.py` for the ESP at
`/boot/efi`. The scratch disk is laid out from the guest's own `sfdisk -d`
and subvolume list, mounted with its fstab options. Real examples: set-id
and capability binaries, `/etc/shadow`, `/etc/selinux/config`, files with
non-default SELinux labels (both tools carry `security.selinux`), `/boot/grub2`,
the ESP's shim and signed GRUB with their FAT attributes, a `C` directory
from `@/var`. Both archivers, both hosts, 0 FAIL;
`stic/manifests.json`, `limits.json` (as tumbleweed's: birth times, project
IDs, snapshots as plain directories), `check-manifests.py`'s count and
`tests/test_stic.py`'s.

**HoltTest1:** `leap16` joins **1Obscure** beside `tumbleweed` (Decision 6):
`classes.json`'s `H.Obscure`, the README's 1Obscure row and HoltTest1All's
members. `HT_SOURCE_PREP` is `prep/tumbleweed-snapper.sh` (snapshots of a
changing root, then a rollback), so the round trip covers snapper's chain,
the old root and a rolled-back default on Leap too. The boot stage runs
under the guest's vm-run options (Secure Boot on) and must come up
`running`, **enforcing**, with no relabel pending (a new boot-state check
for SELinux guests: `getenforce` and the count of AVC denials since boot,
0 required beyond a known list; it also applies to `tumbleweed`, the Fedora
and Rocky guests as an extra line, not a new FAIL for them until each has
passed with it once). judge.py: the boot stage's rewrites (`grub.cfg`, the
dracut images), and **require** `EFI/opensuse/{shim,grub,MokManager}.efi`
and `EFI/boot/bootx64.efi` byte-identical to the source's (the Fedora arm's
promise).

**Test2:** a golden plan in `holt/tests/corpus/leap16/` from a `bko-holt
dump` of the guest (`src.json`, `plan.txt`, `index.tsv`, the corpus README);
`classes.json` (`uefi`, `H.Obscure`), `holt/spec.json`
(`"leap16": {"kind": "linux"}`), `guest/spec.json` (tumbleweed's entry with
the Agama profile in its harness), `estimates.tsv` (stic, holt, guest: as
tumbleweed's until measured), `t2lib/plan.py`'s source and guest counts,
`tests/fixtures/p2/world.py` and the re-blessed goldens. Seven other
sessions bump the same counts this week: L3 takes the counts from `main` as
it is when L3 lands, not from this spec. Done when two corpus builds have
the same sha256, leak scans are 0 on both hosts and the Mac, no other plan
changes, and Test2's own tests pass.

**BackShot1** (optional, L7): the Thinkpad reinstalled with Leap 16.1 from
Agama's UI (Btrfs with snapshots, SELinux, Secure Boot as the Thinkpad has
it). Fixes, est.: `bs1-adopt` a zypper arm if Tumbleweed's doesn't fit
(*check*: no Tumbleweed BackShot1 has run), a hold of snapper's timers during
the backup (93978fd), `bs1-manifest` EXPECTED as above. A
`bs1-qemu.py --virtio` rehearsal on `leap16` on both hosts first.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| L1 | Fetch the offline ISO, `.sha512`, `.sha512.asc` into rebuild-inputs on Chopin (the user's OK; size *check*, ~4-5 GB est.); read it offline (os-release, kernel, dracut's image names, GRUB and shim files, Agama's version, product ID and `inst.finish` values, the default proposal). Then `make-opensuse.sh`'s Leap mode, `opensuse/agama-leap.json`, the install under OVMF with Secure Boot, the relabel and check boots, `rebuild-one.sh`, copy to Cinqcent (same SHA-256), in-guest.sh's Leap arm and `vm-update --try`. Settle every *check* and write "What L1 found" here. Done when `leap16` boots alone, enforcing, with Secure Boot on, on both hosts, and `--try` is ok | 1.25 d |
| L2 | bko-holt (High-effort subagent): the `SUPPORTED` row and tests (16.x accepted, 15.x refused, a Tumbleweed os-release still Tumbleweed); the GRUB-through-shim arm's note named for the system, and unit tests for Leap UEFI (no `grub2-install`, `grub2-mkconfig`, entries) and BIOS (Tumbleweed's arm); `detect_loader` for `\EFI\opensuse\shim.efi` if L1 shows it needs anything. Done when `cargo test` passes and no golden plan changes | 0.25 d |
| L3 | The golden plan and Test2 registrations (as A3): the dump on the live guest under its own vm-run options, the corpus entry, two builds the same, leak scans, 1Obscure, `uefi`, the specs, estimates, counts, p2 goldens | 0.4 d |
| L4 | SticTest1 entry and runs, both archivers, both hosts, 0 FAIL, under `cmp-run.sh` reservations in /mnt/bko-ram | 0.35 d |
| L5 | HoltTest1 1Obscure runs of `leap16` on Chopin and Cinqcent with `tumbleweed-snapper.sh` as prep, the byte-identical ESP rule, the SELinux boot-state line, the boot alone under Secure Boot; RAM peaks in `ram-sizes.tsv`; finished guests moved off the RAM disk | 0.4 d |
| L6 | Docs: holt/README.md's supported list, `osrel.rs`'s comment, HoltTest1's 1Obscure row and HoltTest1All, SticTest1's and Test2's READMEs, `layload.md`'s boot stage, `testvm/HOSTS.md` if needed, memory (holttest1d index, a leap16-support note) | 0.1 d |
| L7 | *Optional, later:* BackShot1 on the Thinkpad | 0.5 d + ~1 h of the user's time |
| | *≈ 2.75 d without L7, ≈ 3.25 d with it* | |

Each piece is committed on main when its tests pass and the user says to
commit; nothing is pushed until the user says so. L3 and L5 touch
`classes.json`, the Test2 goldens, `holt/tests/corpus/` and the HoltTest1
README, which the other seven distribution sessions are editing on main
too: each goes in as its own hunk, on what main has then.

**Costs** (est.): L1 a 20-40 min install on Chopin (`--reserve 10 4` in
/mnt/bko-ram, WEAR_TAG `build:leap16`), the copy; qcow2 ~4-6 GiB a host,
the ISO a few GB a host. L4 and L5 about 1 h of queue a host. No time of
the user's but the OKs, until L7.

## Risks

- **Agama is young and moving.** `inst.finish`'s values are undocumented,
  16.0 had a scripts-path bug, and the profile schema changes between
  releases (Agama 16, 17, ...). L1 pins the ISO, so the guest is
  reproducible; a later 16.x rebuild may need profile changes.
- **Agama headless.** It is a web UI over a live system; unattended it
  needs nobody, but a failed profile makes it wait for an answer. The build
  times out and keeps the serial log and Agama's logs.
- **Secure Boot in OVMF:** SUSE's shim must boot under OVMF's
  Microsoft-keyed store on all four hosts (Ubuntu's and Fedora's do; Leap's
  is the same CA). Should 2026's revocations (SBAT, the 2011 CA's expiry)
  refuse an older shim, L1 records it and the guest runs with Secure Boot
  off until a fixed shim comes through updates.
- **SELinux after a restore:** labels come back with the files, but a
  denial on first boot (a label the restore wrote wrongly, or one the boot
  stage made) would pass today's checks unseen; hence L5's AVC line.
- **Snapshot growth:** zypper's pre/post pairs per vm-update (Decision 5).

## Out of scope

- Leap 15.x (Decision 2): AppArmor, YaST and the older stack; refused by
  version.
- **Immutable mode** (Decision 4): transactional-update over a read-only
  root, the new Leap Micro replacement.
- LUKS2 and TPM-sealed full-disk encryption (Agama offers both): a later
  `leap16-luks` if the user wants it.
- systemd-boot / grub2-bls, arm64, ppc64le, s390x.
- Changes to the rescue image. BackShot2 cases on Leap.

## Decisions for the user

1. **The guest:** one guest, `leap16`, UEFI with Secure Boot, Agama's
   default Btrfs-with-snapshots layout, SELinux enforcing, no desktop
   (recommended: the openSUSE UEFI/shim path is the untested one, and
   `tumbleweed` has BIOS); or also a BIOS guest (+0.5 d, little new); or
   also `leap16-luks` with a typed LUKS2 passphrase (+0.75 d).
2. **Versions:** 16.x only, starting with 16.1, refusing 15.x
   (recommended: 15.6 is near its end and on the old stack); or also 15.6
   (a YaST/AutoYaST guest, est. +0.75 d).
3. **The boot stage:** Fedora's GRUB-through-shim arm as it is (keep the
   ESP, `grub2-mkconfig`, entries; recommended: it already does what Leap
   needs, and Tumbleweed's BIOS arm already uses `grub2-mkconfig`); or
   openSUSE's own `update-bootloader --refresh` in the chroot (follows the
   distribution, but perl-Bootloader may also rewrite the ESP's files).
4. **Immutable mode:** leave it out for now (recommended: it is a separate
   install mode with a read-only root and transactional-update, a spec of
   its own after this lands); or add `leap16-immutable` now (est. +1 d).
5. **Snapshots in the image:** keep zypper's pre/post pairs as Tumbleweed's
   image does, with `snapper cleanup number` at the end of each update if
   `--try` shows them piling up (recommended); or turn snapper-zypp-plugin
   off for vm-update.
6. **HoltTest1's word:** **1Obscure**, beside `tumbleweed` (recommended:
   the openSUSE guests stay together without a new word); or a new **1S**
   (SUSE: `tumbleweed` moved there with `leap16`; more README and
   classes.json churn while seven other sessions edit them).
7. **The ISO:** download Leap 16.1's offline installer ISO with its signed
   `.sha512` to Chopin's rebuild-inputs (recommended: verified against the
   openSUSE key as Tumbleweed's DVD is); or the user downloads it.
8. **BackShot1 on the Thinkpad (L7):** later, after L1-L6 pass
   (recommended: the Thinkpad is in use for BackShot1All, and the install
   takes about an hour of the user's time); or now; or not at all.
