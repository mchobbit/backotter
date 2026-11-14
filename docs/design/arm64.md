# ARM64 support

2026-11-14. Asked for by the user: look into ARM64 support for BackOtter, then
write the spec, using a folder within `/home` on Bigfoot as the staging area
for the port. The user approved this spec on 2026-11-14 with every
recommended decision, and added one: a dedicated ARM64 machine similar to the
MS-R1 is to be bought (Decision 11).

**Conventions.** est. marks an estimate. *Check* marks a fact the sources
don't settle, to be settled in A0 or on the first guest before the code that
depends on it. Code references are to local `main` at db15d72 plus the
uncommitted tree. Bigfoot's facts were read over ssh on 2026-11-14 (read-only:
`df`, `id`, `lscpu`, `command -v`, `/dev/kvm`); nothing was installed or
changed there.

## Summary

- **Scope (phase 1):** UEFI ARM64 Linux, the machines that boot like a PC:
  servers, cloud and QEMU `virt` guests, and MS-R1-class mini PCs. The work
  covers:
  - both archivers (restic-bko, rustic-bko) and bko-holt built for ARM64
  - one ARM64 Linux rescue image with ZFS
  - five ARM64 guests, with SticTest1 and a new HoltTest1 word, **1ARM**
- **Phase 2** (its own spec, later): FreeBSD arm64.
- **Out of scope:**
  - Raspberry Pi and U-Boot boards such as Coventry's RK3588, whose boot
    firmware is a separate world.
  - illumos, Haiku, DragonFly, OpenBSD and NetBSD on ARM64.
- **Host:** Bigfoot, the only ARM64 machine with KVM (12 cores, 62 GB, Debian
  13, `/dev/kvm` present). It joins the farm as a small-class host with a new
  `arch: arm64` field. It stays out of default runs until HostTest2 passes on
  it.
- **Staging:** every piece of bko state on Bigfoot lives in one folder,
  **`/home/m/bko-arm64/`**, on the NVMe's own 838 GB `/home` partition
  (ext4, 757 GB free). Nothing goes on DantePool or into the vm-store master.
  Removing bko from Bigfoot means deleting that folder, two symlinks and one
  fstab line.
- **The code ports easily, and the farm is the work.**
  - The archivers and bko-holt have no architecture-specific code: no
    `target_arch`, raw ioctl numbers or `asm!` in `holt/src` or `stick/`.
  - What is x86-bound is boot handling (`BOOTX64.EFI`, `x64`/`x86_64-efi`
    names, BIOS paths), the build targets, the image's squashfs and firmware
    choices, and `vm-run`'s `qemu-system-x86_64` with OVMF.
- **Size:** 12.5 days est. (A0-A6), plus waits on the user's approvals for
  Bigfoot.

## Why

- ARM64 now ships in servers (Ampere, Graviton-class), mini PCs like the
  user's MS-R1, and Linux laptops. A whole-machine restore tool that can't
  dump or load them leaves those users out.
- The archivers are nearly free to port. bko-holt and the rescue image are
  where restores actually fail, so they need real guests and round trips, not
  just a cross-compile.

## What already works, and what doesn't

### Already ready

- **restic-bko:** Go cross-builds with `GOARCH=arm64`. `build-backotter.sh`
  takes `os/arch` pairs; `stick/build-round.sh:541` passes only `os/amd64`
  today.
- **rustic-bko:** pure Rust. `aarch64-unknown-linux-musl` is a tier-2 Rust
  target. Its C dependencies (zstd, the TLS crypto) build for ARM64 natively.
- **bko-holt:** pure Rust, built static against glibc
  (`x86_64-unknown-linux-gnu`, `+crt-static`, `testvm/holttest1d/build-holt.sh:189`).
  The ARM64 equivalent is `aarch64-unknown-linux-gnu`.
- **Names already known:**
  - `holt/src/boot.rs:260` and `:1230` already list the `grub-efi-arm64`
    packages.
  - `holt/src/redact.rs:200` treats `arm64`/`aarch64` as safe words.
  - `stick/make-img.sh:81` reads `amd64|arm64` from the Ubuntu ISO and names
    the image `backotter-live-linux-VERSION-arm64.img`.
  - `docs/design/stick-config.md:165` defines `arch` as `amd64` or `arm64`.

### x86-bound today

| Where | What | ARM64 needs |
|---|---|---|
| `holt/src/boot.rs` (61 x86 references), `load/commands.rs` (51), `scenarios.rs` (117, mostly tests) | `EFI/BOOT/BOOTX64.EFI`, `grubx64`/`shimx64`/`mmx64`, `x86_64-efi`, the BIOS (`i386-pc`) path, `limine_x64.efi` | `BOOTAA64.EFI`, `grubaa64`/`shimaa64`/`mmaa64`, `arm64-efi`, `systemd-bootaa64.efi`, `refind_aa64.efi`, Limine's `BOOTAA64.EFI`. No BIOS path at all. |
| bko-holt's dump and load | the dump doesn't record the machine's architecture | dump records `uname -m` and the firmware's architecture. load refuses a dump of another architecture by name, and refuses a rescuer of another architecture. |
| `stick/build-round.sh:574`, `build-holt.sh:189` | one x86_64 target each | an ARM64 target and its own digest rows |
| bin directories (`bko-bin/linux/`, `holt-bin/linux/`) | keyed by OS only | `linux-arm64/` beside the unchanged `linux/` (Decision 4) |
| `stick/build-backotter-layer.sh` | `chroot`s into the base rootfs to run apt, uses `-Xbcj x86`, and hard-codes the DKMS path `x86_64` (`:302`) | a native ARM64 build host (a chroot runs the rootfs's own binaries), `-Xbcj arm64` (*check*: squashfs-tools ≥ 4.6 with xz ≥ 5.4), and `aarch64` |
| `stick/slim-base-layer.sh:25` | drops ARM firmware ("the stick is amd64-only") | keeps the firmware an ARM64 UEFI machine needs, drops x86-only firmware |
| `testvm/vm-run:276`, `:300-311` | `qemu-system-x86_64 -machine q35`, OVMF from `/usr/share/OVMF`, SeaBIOS for `--bios` | `qemu-system-aarch64 -M virt,gic-version=host -cpu host`, AAVMF from `/usr/share/AAVMF` (`qemu-efi-aarch64`), and `--uefi` implied, with BIOS refused |
| about 50 files naming `qemu-system-x86_64`, 59 naming OVMF | harness checks and messages | read the host's `arch` from `hosts.json`; most need only the binary name |
| `testvm/hosts.json` | no architecture per host | `"arch": "amd64"` by default; Bigfoot's row says `arm64`. t2 places a guest only on a host of its architecture, as `needs_host_zfs` (f0def4f) does for ZFS. |
| guest builders (`make-debian.sh`, `make-ubuntu.sh`, `make-fedora.sh`, `make-arch.sh`'s Alpine path) | x86 ISOs and `amd64` names | `--arch arm64`: Debian netinst arm64, Ubuntu Server arm64 autoinstall, Fedora aarch64 kickstart, Alpine `virt` aarch64 |

## Bigfoot as the ARM64 host

What it has (2026-11-14):
- Minisforum MS-R1: CIX P1, 12 cores in a mixed big/little layout. `lscpu`
  shows CPUs 2-5 at 1.8 GHz (likely the A520s, *check*) and 0-1, 6-11 at
  2.2-2.6 GHz (the A720s).
- 62 GB RAM (about 52 free), Debian 13, kernel 6.19.10, OpenZFS installed,
  `/dev/kvm` present.
- `/` is 55 GB ext4. `/home` is its own 838 GB ext4 partition on the same
  990 PRO, with 757 GB free.
- User `m` has sudo but is not in the `kvm` group.
- Missing: `qemu-system-aarch64`, AAVMF, swtpm, xorriso, squashfs-tools,
  debootstrap, Go and rustup. Debian's own cargo is 1.85.0.

Its other roles constrain everything here:
- It is the user's daily machine: a desktop session (lightdm), deluge, Plex,
  miniircd instances and hugos-heartbeat.
- It holds the vm-store masters on DantePool, whose USB housings are being
  replaced the week of 2026-11-16.

The port must stay out of the way of all of that.

### The staging folder

```
/home/m/bko-arm64/
  work/       BKO_WORK: sync-hosts.sh's testvm/ and stick/, bko-bin/, holt-bin/, vms/, runs
  scratch/    what /bkoscratch is on the other hosts (images, rebuild-inputs, holttest1d)
  toolchains/ RUSTUP_HOME, CARGO_HOME, GOROOT: rustup 1.94.1 + 1.85.1, Go as pinned elsewhere
  README      what this folder is, who made it, and how to remove it
```

- **Symlinks:** `~/backotter-work -> /home/m/bko-arm64/work`, and
  `/bkoscratch -> /home/m/bko-arm64/scratch`. The second needs sudo once.
  142 files name `/bkoscratch` and 57 default `BKO_WORK` to
  `~/backotter-work`, so symlinks beat changing the scripts.
- **No reflinks:** ext4 has none. qcow2 overlays work, and vm-store's copy
  falls back to `VM_STORE_COPY=plain`.
- **RAM disk:** a 10 GiB `bko-ram` at `/mnt/bko-ram`, as on Cage and Copland,
  in fstab (sudo).
- **Toolchains:** in the folder, not in `~/.cargo` or `/usr/local`. No sudo,
  and they go away with it.
- **Never:** the scripts never touch DantePool, `bt/BkoStore`, or
  Chopin's `bigfoot-link` push path. `/bkoscratch` on Bigfoot holds no
  `store.json` until ARM64 guests are published (Decision 9), so vm-store
  can't mistake the staging area for a store root.

### Keeping it out of the user's way

- **A cgroup slice:** every bko job runs in a `bko.slice` systemd slice. It
  gets `CPUQuota=600%` and `AllowedCPUs=` set to the A720 cores (*check*),
  `MemoryMax=24G` and `IOWeight=20`. `cmp-run.sh` and `vm-run` start through
  `systemd-run --user --slice=bko.slice` when the host's row says so.
- **Limits:** small class, so 2 stic slots, 1 holt job and `base_gib` 4.
- **Big/little cores:** on a mixed-core CPU, a KVM guest with `-cpu host` must
  keep its vCPUs on one core type, or the guest can see CPU ID registers
  change under it. `vm-run` pins QEMU to the big cores on such hosts. A0
  proves this works (*check*).
- **Reboots:** next week's disk move may reboot Bigfoot. Jobs there must be
  restartable, as they are on the other hosts.

## What A0 found (2026-11-14)

The setup is done on Bigfoot as Decision 2 approved it. The staging
folder's `README` lists every change outside it and how to undo each one.

- **Installed:** the seven packages, plus `qemu-utils` for `qemu-img`,
  which the spec's list missed. QEMU is 10.0.13, from the
  `qemu-efi-aarch64` firmware build of Debian 13.
- **Groups and paths:** `m` is in `kvm`. `/bkoscratch` and `~/backotter-work`
  are symlinks into `/home/m/bko-arm64`. The 10 GiB `bko-ram` tmpfs is
  mounted at `/mnt/bko-ram`, and the old fstab is backed up as
  `/etc/fstab.bak-20261114-bko`.
- **`bko.slice` is a system slice, not a user slice.** Bigfoot's user manager
  delegates only cpu, memory and pids, so a user slice couldn't set
  `AllowedCPUs` or `IOWeight` without restarting the desktop session's
  manager. Jobs start with
  `sudo systemd-run --scope --quiet --slice=bko.slice -- setpriv --reuid=m --regid=m --init-groups --reset-env -- CMD`.
  The scope runs in the foreground with the caller's stdio. `setpriv
  --init-groups` is needed, because `systemd-run --uid=m` keeps sudo's
  `root` group and drops `kvm`. Checked: the job runs as `m` with
  `kvm`, on CPUs 0-1 and 6-11, with CPUQuota 600%, MemoryMax 24G and
  IOWeight 20. `sudo -n` works on Bigfoot.
- **Core types (settled):** MIDR part 0xd81 (Cortex-A720) is CPUs 0-1 and
  6-11; part 0xd80 (Cortex-A520) is CPUs 2-5.
- **KVM works.** Debian 13's arm64 genericcloud image boots under
  `qemu-system-aarch64 -M virt,gic-version=host -cpu host -enable-kvm` with
  AAVMF, and SSH is up in 18-21 s. Four parallel hashes in the guest agree,
  and the host logs no KVM messages.
- **Pinning matters (settled):** pinned to the A720s, the guest sees 4× part
  0xd81. Unpinned, the same guest sees 2× 0xd80 and 2× 0xd81, a mixed CPU it
  happens to tolerate. vm-run pins on mixed-core hosts.
- **Secure Boot works (settled, Decision 10):** with `AAVMF_CODE.ms.fd` and
  `AAVMF_VARS.ms.fd`, Debian's shim and GRUB boot, and the kernel reports
  "locked down from EFI Secure Boot". With `AAVMF_CODE.no-secboot.fd` it
  reports "Secure boot disabled". Debian also ships `secboot`, `snakeoil`
  and `strictnx` variants.
- **NIC boot ROM:** `virtio-net-pci` wants `efi-virtio.rom` from
  `ipxe-qemu`, which wasn't installed. vm-run passes `romfile=` on aarch64,
  because the guests never network-boot.
- **No arm64 BCJ filter (settled):** Debian 13's mksquashfs 4.6.1 rejects
  `-Xbcj arm64`, though xz is 5.8.1. A4 uses plain xz on arm64.
- **Toolchains:** rustup 1.94.1 (with `aarch64-unknown-linux-musl`) and
  1.85.1 are in `toolchains/rustup` (`RUSTUP_HOME`). They use the rustup
  binary already in `~/.cargo/bin`, and `~/.rustup`'s own default is left
  alone. Go isn't installed on Bigfoot: restic-bko arm64 is cross-built on
  Chopin and Cinqcent.
- **Not done in A0:** `sync-hosts.sh bigfoot`, which needs Bigfoot's
  `hosts.json` row from A1.

## What A1 built (2026-11-14)

- **The table.** `testvm/hosts.json` rows have an optional `arch`
  (`amd64`, the default, or `arm64`) and `job_slice`. Bigfoot's row is
  `arch: arm64`, `job_slice: bko.slice`, small class, slots 3200-3299
  (stic ports 3280-3299), `ip` 192.168.68.197, no roles and
  `default: false`.
- **A guest's architecture** is in its name: `arm64` for `*-arm64`, else
  `amd64` (hosts.py `guest_arch`).
- **Readers.** hosts.py has `host_arch`, `guest_arch`, `runs_on`,
  `machine_arch`, `job_slice`, `job_prefix` and `names(arch=)`. Its command
  line adds `arch`, `guest-arch`, `job-prefix` and `names --arch`. hosts.sh
  wraps them as `hosts_arch`, `hosts_guest_arch` and `hosts_job_prefix`.
- **Placement** (`plan.py`) keeps a stic or holt cell to hosts of its guests'
  architecture, before any facts check. Facts whose `machine` contradicts
  the table also keep a host off. Gate cells stay on amd64 hosts until A6,
  and `vm-update`'s guest cells follow the guest's architecture.
- **The consumers that would mix architectures** take only amd64 hosts, or
  refuse an arm64 one by name: bs2, run-all.sh (and its `SPLIT=all`),
  build-round.sh `--dist-hosts`, promote-bins.sh, and holt's `holt_ship
  known`. drive.py drops another architecture's images from each host's
  probe and refuses a guest with no host of its architecture. ht1-queue.sh
  skips such lines, and vm-update merges a guest only on hosts of its
  architecture. sync-hosts.sh includes Bigfoot and takes a bare table name.
  vm-store doesn't read the table.
- **vm-run** on aarch64 runs `qemu-system-aarch64 -M virt,gic-version=host
  -cpu host -enable-kvm`, with UEFI always on.
  - Firmware: AAVMF's `no-secboot` code with `AAVMF_VARS.fd` by default, and
    `--uefi` for `ms` code and vars (Secure Boot).
  - Devices: `romfile=` on the NIC, `tpm-tis-device`, a virtio-scsi CD for
    `--cdrom`, and an added `ich9-ahci` for sata.
  - Refused: `--bios` and `--q35`.
  - Pinning: on cores of more than one MIDR part, vm-run pins QEMU with
    taskset to the most numerous part among the allowed CPUs, unless they
    are all one part already.
  - The slice: on a host with a `job_slice`, vm-run starts itself again in
    it, with the same environment and working directory.
  - x86: the QEMU arguments are unchanged in 32 option sets compared with the
    old vm-run.
- **cmp-run.sh** puts its scopes in the host's slice. vm-lib.sh follows QEMU
  through the slice's sudo.
- **Facts.** t2's host facts record `machine` (`uname -m`).
- **Checked on Bigfoot.** The Debian 13 arm64 cloud image, in an overlay,
  booted through vm-run:

  | Run | Slice | SSH up | What the guest saw |
  |---|---|---|---|
  | No Secure Boot | bko.slice (CPUs 0-1, 6-11) | 18.9 s | 4 vCPUs, all part 0xd81 |
  | `--uefi` | bko.slice | 19.1 s | "locked down from EFI Secure Boot", SecureBoot=1 |
  | Outside the slice | none | 18.9 s | pinned by taskset to CPUs 0,1,6-11, all part 0xd81 |

  vm-lib.sh's `vm_boot` and `vm_halt` worked through the slice's sudo too.
  cmp-run.sh's ledger self-test (`cmp-run-test.sh`) passed there, and its
  `--reserve` jobs ran as `m` with `kvm` in bko.slice, both with and without
  `WEAR_LOG=off`.
- **Left x86-only.** The make-*.sh builders, stick/'s build and boot-test
  scripts, the compare drivers (guest-run.sh, run-guest-rt.sh,
  zfs-guest-rt.sh, nonroot-rt.sh), ht1-lib.sh and ht1-lib-test.sh,
  bs2lib/reuse.py, rw1a-qemu.py, sculpt-check.sh, rows_holt.py (it records
  the host's qemu-system-x86_64 version), and the store's vm-run-guard.sh.
  vm-mount and vm-chroot name no QEMU binary and needed no change. A3-A6
  take the rest up where an ARM64 guest needs them. bko-bin, cmp-bin,
  holt-bin and the rounds are amd64, so Bigfoot gets none of them until A2.

## Pieces to build (ordered, est.)

| # | Piece | Est. |
|---|---|---|
| **A0** | **Bigfoot setup and probe.** One sudo batch the user approves (Decision 2): the packages `qemu-system-arm qemu-efi-aarch64 swtpm swtpm-tools xorriso squashfs-tools debootstrap`, `m` added to `kvm`, the `/bkoscratch` symlink, the `bko-ram` fstab line and `bko.slice`. Then without sudo: the staging folder, the toolchains and `sync-hosts.sh bigfoot`. Probe: boot a stock Debian 13 arm64 cloud image under `qemu-system-aarch64 -M virt -cpu host` with AAVMF, pinned and unpinned, and settle the *checks* on core types, AAVMF's Secure Boot variants and `-Xbcj arm64`. | 0.5 d |
| **A1** | **Architecture plumbing.** `hosts.json` gains `arch` (amd64 by default), and Bigfoot gets a row (small class, `default: false`). The harness readers (`hosts.py`, `hosts.sh`) and t2 placement follow it. `vm-run` gets the aarch64 machine, AAVMF, the pinning and a refused `--bios`/`--q35`. vm-lib's needs-checks become arch-aware, and t2's host facts record `uname -m`. Unit tests. | 1.5 d |
| **A2** | **Archivers.** restic-bko `linux/arm64`, cross-built on Chopin and Cinqcent (Go is reproducible, so the two digests must match, as for every platform). rustic-bko `aarch64-unknown-linux-musl`, built natively on Bigfoot, with a second build cross-compiled on Chopin with `cargo-zigbuild` for a digest check (Decision 5). New `linux-arm64/` bin dirs with their own SHA256SUMS, and build-round rows per architecture. | 1 d |
| **A3** | **bko-holt.** Built `aarch64-unknown-linux-gnu` `+crt-static` on Bigfoot. Boot detection and load cover the ARM64 EFI names in the table above. dump records the architecture, and load refuses a mismatch with a message naming both. Fixtures and golden plans for an ARM64 GRUB+shim system and an ARM64 systemd-boot system. | 2.5 d |
| **A4** | **Rescue image.** From the Ubuntu Server 26.04 arm64 ISO, built natively on Bigfoot (slim base, layer, `make-img.sh`). Changes: `-Xbcj arm64`, ARM64 firmware kept, the DKMS path for `aarch64`, `zfsutils-linux` from Ubuntu arm64, and bcachefs only if apt.bcachefs.org has arm64 builds (*check*; without them the image ships without bcachefs, as a noted LIMIT). `boot-test.py` boots it with AAVMF. One image, `backotter-live-linux-VERSION-arm64.img`, per the one-kernel rule. | 2 d |
| **A5** | **Guests,** on Bigfoot's NVMe (Decision 6): `debian13-arm64` (ext4, GRUB UEFI), `ubuntu26-arm64` (shim+GRUB, Secure Boot if AAVMF's MS-key variant works), `fedora44-arm64` (btrfs, BLS), `alpine324-arm64` (the smallest) and `debian13-zfs-arm64` (ZFS root; Bigfoot has OpenZFS). Each builder gains `--arch arm64`. vm-update, vm-mount and vm-chroot work on them, and each guest gets a `vm-update --try`. | 3 d |
| **A6** | **Tests.** SticTest1 entries for all five, with both archivers. HoltTest1 word **1ARM** for the five, with the `+img` variant from A4's image. HostTest2 on Bigfoot. Then the Test2 registrations, in the batched pass like the other new distros. | 2 d |

Total: 12.5 d est. A2 can run alongside A1; A3 and A4 need A1 and A2; A5
needs A1; A6 needs everything.

## Tests

- **Unit:** `hosts.json` arch parsing and t2 placement, so an `-arm64` guest
  never lands on an amd64 host and the reverse. vm-run's argument checks.
  bko-holt's arch refusal. Boot detection on ARM64 fixtures.
- **Builds:** restic-bko arm64 digests match across Chopin and Cinqcent.
  rustic-bko arm64's Bigfoot and Chopin-cross digests match, or the mismatch
  is explained.
- **SticTest1:** each phase-1 guest, both archivers, 0 FAIL, the usual
  limits.
- **HoltTest1 1ARM:** each guest's round trip, with the restored disk booting
  alone under AAVMF, plus one `+img` round trip from the shipped ARM64 image.
- **Cross-arch refusal:** loading an amd64 dump from the ARM64 rescuer, and
  an ARM64 dump from the amd64 rescuer, each fail early, before any disk is
  written.
- **No real-hardware run in phase 1.** Bigfoot can't be its own test subject,
  and Coventry boots through U-Boot. A BackShot1-style run on an ARM64
  machine waits for a spare one.

## Risks

- **Bigfoot's other roles.** Guest load could make the desktop, Plex or
  deluge worse. The slice caps it, and the user can stop it with one
  `systemctl --user stop bko.slice`.
- **KVM on the CIX P1.** Mainline support for this SoC is young. If KVM guests
  are unstable, A0 finds out on day one. The fallbacks are the Mac's M5 Pro
  (HVF, outside the farm's scripts) or a dedicated ARM64 box (Decision 1).
- **Single host.** With only Bigfoot, ARM64 guest round trips lose the
  two-host cross-check the x86 guests get from Chopin and Cinqcent. A second
  ARM64 host would restore it.
- **Image size.** ARM64 firmware is large. Keeping all of it could push the
  image well past the amd64 one. A4 keeps the generic UEFI-server set and
  measures the result.
- **Boot names multiply.** Every new loader name in boot.rs now has an ARM64
  twin. A3 puts the names in one per-architecture table rather than adding
  `aa64` cases next to each `x64` one.

## Out of scope

- FreeBSD arm64 (phase 2, its own spec), and the other BSDs, illumos, Haiku
  and DragonFly on ARM64.
- Raspberry Pi firmware boot (`config.txt`, `kernel8.img`) and U-Boot boards
  (extlinux.conf, boot.scr, vendor kernels).
- 32-bit ARM, RISC-V.
- Publishing ARM64 guests to vm-store (Decision 9).
- Changing any existing x86 path, name or bin directory.

## Decisions for the user

1. **ARM64 host:** Bigfoot, staged in `/home/m/bko-arm64/`
   (**recommended**). The alternatives are a dedicated ARM64 box (a second
   MS-R1 or similar), or the Mac's M5 Pro, which is outside the farm's Linux
   scripts.
2. **Bigfoot system changes, one sudo batch:** the seven packages, `m` in
   `kvm`, the `/bkoscratch` symlink, the 10 GiB `bko-ram` fstab line and
   `bko.slice`. **Recommended: yes.** Nothing else on Bigfoot changes.
3. **Resource caps:** 6 CPUs' worth on the big cores, 24 GiB, IOWeight 20,
   and small-class limits. **Recommended.**
4. **Bin layout:** add `linux-arm64/` and leave `linux/` as amd64
   (**recommended**: nothing existing moves), or rename every directory to
   `OS-ARCH`.
5. **rustic-bko ARM64 builds:** native on Bigfoot, plus a `cargo-zigbuild`
   cross-build on Chopin for a second digest (**recommended**). The
   alternative is Bigfoot only, with no cross-check.
6. **Phase-1 guests:** the five in A5 (**recommended**), or only
   `debian13-arm64` and `alpine324-arm64` to start.
7. **HoltTest1 word:** **1ARM**, kept out of 1All until Bigfoot is a default
   host (**recommended**).
8. **Scope:** phase 1 is UEFI Linux only, FreeBSD arm64 is a later spec, and
   Pi/U-Boot is out (**recommended**).
9. **vm-store:** ARM64 guests stay local on Bigfoot in phase 1, and
   publishing them waits until after the DantePool housing move
   (**recommended**).
10. **Secure Boot on ARM64 guests:** used where AAVMF's MS-key variant boots
    the distro's shim (`ubuntu26-arm64`), and off elsewhere
    (**recommended**, settled in A0).
11. **A dedicated ARM64 host (the user, 2026-11-14):** buy an ARM64 machine
    similar to the MS-R1, so the port doesn't depend on Bigfoot alone and
    ARM64 round trips get a second host. A0-A6 start on Bigfoot as planned,
    and the new machine joins as a second ARM64 host (its own `hosts.json`
    row, `arch: arm64`) once it arrives. The user chose a **Radxa Orion
    O6N** (CIX P1, 32 GB), to be ordered when it is back in stock. The
    MS-R1 itself was sold out at every store checked on 2026-11-14. Until
    the O6N arrives, Bigfoot is the only ARM64 host.
