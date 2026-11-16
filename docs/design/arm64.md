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

## What A2 built (2026-11-14)

- **Layout (Decision 4).** Linux's ARM64 binaries live in `linux-arm64/`
  beside the unchanged `linux/`, in a round's `bin/`, `bko-bin`, `holt-bin`
  and `/bkoscratch/holttest1d/bin`. Each `linux-arm64/` has its own
  `SHA256SUMS` (paths `./NAME`). A round's top `SHA256SUMS` still lists every
  file, `linux-arm64/`'s too, so t2's `round_complete` reads it as before.
- **The table.** Bigfoot's row has one role, `build-arm64`: it builds a
  round's arm64 rustic-bko and arm64 bko-holt. It isn't `build`, so every
  amd64 build loop (promote-bins `--from`, t2's round step, `holt_ship`)
  still leaves it out, and `all` still means the amd64 hosts wherever A1 made
  it so. Rows gain optional `rustup_home` and `cargo_home` (hosts.py
  `rust_env`; hosts.sh `hosts_rust_env [NAME]` exports each one that is
  unset). Bigfoot's are `/home/m/bko-arm64/toolchains/{rustup,cargo}`, so no
  bko build uses `~/.rustup` or `~/.cargo`'s registry there.
- **build-round.sh `--arch amd64|arm64|amd64,arm64|all`** (default amd64,
  which builds what every round built before):

  | binary | shipped build | check build |
  |---|---|---|
  | restic-bko `linux-arm64` | Chopin, Go 1.25.10's cross build (`linux/arm64`) | Cinqcent, the same Go |
  | rustic-bko `linux-arm64` | Bigfoot, natively, in `bko.slice` | Chopin, a cross build |

  - Bigfoot's part runs whole in its `job_slice`, through
    `hosts_job_prefix`. The Mac brings what Bigfoot built (its
    `build-bigfoot.files`) to Chopin and Cinqcent by tar, then sends
    `bin/linux-arm64/` back to Bigfoot from Chopin, checked there by its
    `SHA256SUMS`. Bigfoot's round has no other directory and no top
    `SHA256SUMS`.
  - A check build lands in `roundROUND/check/OS/`, never in `bin/`, and its
    row's RESULT is `check-built` or `check-reused`. "Cross-checks", after the
    table, compares it with the shipped build by SHA-256. A difference fails
    the round, except the explained one below.
  - `--dist-hosts` may name an arm64 host, which gets only the round's
    `*-arm64/` directories. `--sums ROUND` makes a host's `SHA256SUMS` again,
    and `--pull` copies the arm64 directories' ones too.
  - `--arch arm64` needs chopin in `--hosts`. Without cinqcent, the restic
    row says "not cross-checked".
- **Both rustic-bko builds go through cargo-zigbuild**, with zig as the C
  compiler and linker. Bigfoot has no musl C compiler: cc-rs looks for
  `aarch64-linux-musl-gcc`, and a plain native build fails in zstd-sys.
  Installing `musl-tools` would be a system change.
  - `stick/zig-tools.sh --install` puts zig 0.15.2 and cargo-zigbuild 0.24.1
    in `~/backotter-work/tools`, for user m, on Chopin and on Bigfoot. Zig's
    tarball sha256 is pinned per machine, and both tarballs' minisign
    signatures were checked against Zig's key on Chopin. cargo install
    `--locked` checks crates.io's checksums.
  - `stick/zigbuild/cargo` turns build-backotter.sh's `cargo build` into
    `cargo zigbuild`, so the fork's script runs unchanged.
  - The tree's, `CARGO_HOME`'s and zig's paths are remapped
    (`--remap-path-prefix`, `-ffile-prefix-map`). Without that, about 1,200
    panic messages carry the absolute registry and tree paths.
  - C's `__DATE__` and `__TIME__` are fixed, because mimalloc prints its
    build time.
  - zig's and cargo-zigbuild's caches live in `build-cache`
    (`ZIG_*_CACHE_DIR`, `XDG_CACHE_HOME`), never in `~/.cache`.
- **Set up for it.** Chopin's rustup 1.94.1 got the
  `aarch64-unknown-linux-musl` target. Cinqcent got Go 1.25.10 in `~/sdk`,
  copied from Chopin's, with the same tree digest (`58af9f27...`). On Bigfoot
  the only change was inside the staging folder (README updated): the tools,
  `toolchains/cargo`, `work/build-cache`, and the `cmp-run.sh` link.
- **bko-holt `linux-arm64`.** `build-holt.sh linux-arm64`, and
  `--reserved RUN linux-arm64`, are the twin of its linux build:
  `aarch64-unknown-linux-gnu`, `+crt-static`, `HOLT_RUST_TOOLCHAIN` (1.94.1
  by default, as for amd64). It builds natively on the host, as linux's build
  does, and not in a build guest: the guests exist only for the systems that
  can't build on the host. It refuses an x86_64 host. It takes the row's
  toolchain homes and runs cargo in the host's `job_slice`, unless it is
  there already (cmp-run.sh's scope is). Its output goes to
  `/bkoscratch/holttest1d/bin/linux-arm64/`.
- **Readers.**
  - promote-bins.sh: an arm64 host, when named, gets only each set's
    `*-arm64/` directories, and cmp-bin, which has none, is left alone there.
    An amd64 host gets each set whole, as before.
  - drive.py: an `-arm64` guest's binaries are `bin/linux-arm64`'s.
  - build-backotter-layer.sh: `BKO_ARCH=arm64` takes `linux-arm64/`. Each of
    the three binaries, and the base's `/usr/bin/dash`, must be an ELF of
    `BKO_ARCH`'s machine. A4 does the rest of the arm64 layer.

### The builds

Round `a2arm64-3` was built from the forks' HEADs: restic `aa72d65cceea`,
rustic `a63bf7b4aed1`, rustic_core `fd7062d01f0f`, which are bko-bin rel8's
commits. It was built from clean clones, with the scripts copied to each
host's `~/backotter-work/a2-scratch`. Release profile, Rust 1.94.1.

| binary | host | toolchain | SHA-256 | input digest |
|---|---|---|---|---|
| restic-bko linux-arm64 | chopin | go1.25.10, `CGO_ENABLED=0 -trimpath` | `b79fe55a8896d8dd3d50181804aa8b8fc04be93df038e9068d4bfb2f2fb50ff7` | `c44bab8efb23...` |
| restic-bko linux-arm64 (check) | cinqcent | the same | the same | the same |
| rustic-bko linux-arm64 | bigfoot | rustc 1.94.1 aarch64 host, zig 0.15.2, cargo-zigbuild 0.24.1 | `bdfbda6dc234ff010fb3bb742aef8f1f20076548eb09229b903119c354b95ab0` | `1361e236ecb0...` |
| rustic-bko linux-arm64 (check) | chopin | rustc 1.94.1 x86_64 host, the same zig and cargo-zigbuild | `f6d8207cf91c4444c0c0da0c1b5a63bd60f286964e6289f39389339c4a47c7e3` | `63af0f66f494...` |
| bko-holt linux-arm64 | bigfoot | rustc 1.94.1, holt `5b2fe9de` (holt-bin linux's commit), `+crt-static` | `908ea6ea88e32b5051bdba7a0fba72d3a3639af3ec61e7048712cf4c1ca34bb6` | none |

What the builds showed:

- All three are static aarch64 ELFs, and all three run on Bigfoot.
- Each host built the same rustic bytes in two rounds.
- Two builds of bko-holt, one under cmp-run.sh and one direct, are the same
  bytes.
- On Bigfoot, restic-bko and rustic-bko each backed up and restored a small
  tree with `diff -r` clean. Chopin's cross-built rustic-bko restored
  Bigfoot's repository the same way.

**Why the two rustic-bko builds differ.** It is neither the linker nor the
paths, and it can't be fixed by flags:

- **C.** Once zig's directory was remapped and `__DATE__`/`__TIME__` fixed,
  all 435 C objects and the 5 C archives (aws-lc, ring, zstd, mimalloc) are
  the same bytes on both hosts.
- **Rust.** Of the 478 rlibs, 192 have the same name (crate hash) on both
  hosts, and all 192 have the same object code. Only their `lib.rmeta`
  differs, and that never reaches the binary. The other 286 have different
  crate hashes. cargo hashes `rustc -vV`, host line included, into every
  unit built for the host: build scripts and proc-macros. Those hashes then
  pass into the `-C metadata` of each crate that depends on one (for example
  `abscissa_core`, through `abscissa_derive`). A different crate hash means
  different symbol hashes, so code and data are laid out in another order in
  the LTO'd binary: the strings are the same, their order isn't.

A build on an x86_64 host and one on an aarch64 host can't be the same
bytes. So build-round says so in its Cross-checks line ("differs, as builds
on an arm64 and an amd64 host must") and fails nothing for that case. A
difference between two hosts of one architecture, such as restic's, still
fails the round. A second ARM64 host (Decision 11's O6N) would give
rustic-bko a byte-for-byte check.

**Bigfoot's bins.**

- `~/backotter-work/bko-bin/linux-arm64` holds restic-bko, rustic-bko, their
  commit files, a `BUILD-INFO` and a `SHA256SUMS`.
- `~/backotter-work/holt-bin/linux-arm64` holds bko-holt, `COMMIT` and a
  `SHA256SUMS`.
- Bigfoot has no amd64 binary.
- Chopin's master sets were left unchanged. Adding `linux-arm64/` there
  (then `promote-bins.sh bigfoot`) is the user's promotion to make. It would
  change the sets every amd64 host and t2 read.

**Tests.**

- `testvm/test2/tests/test_arm64_builds.py` (28 tests) covers `rust_env`,
  zig-tools.sh and the cargo shim, `--sums` and `--pull`, the Mac part with
  `--arch arm64` against fake hosts (the slice, the copies, the
  cross-checks, refusals, the dry run, amd64 by default), `--dist-hosts` and
  promote-bins.sh to an arm64 host, and build-holt.sh's `linux-arm64`.
- drive.py's self-test covers its new checks.
- test_arch.py and test_round_dist.py were updated.

## What A3 built (2026-11-14)

- **The table.** `holt/src/arch.rs` has one row of names for each
  architecture, `arch::X86_64` and `arch::AARCH64` (struct `Names`):

  | field | x86_64 | aarch64 |
  |---|---|---|
  | `machine`, `debian`, `efi` | `x86_64`, `amd64`, `x64` | `aarch64`, `arm64`, `aa64` |
  | `fallback` | `EFI/BOOT/BOOTX64.EFI` | `EFI/BOOT/BOOTAA64.EFI` |
  | `grub`, `shim` | `grubx64.efi`, `shimx64.efi` | `grubaa64.efi`, `shimaa64.efi` |
  | `systemd_boot`, `refind` | `systemd-bootx64.efi`, `refind_x64.efi` | `systemd-bootaa64.efi`, `refind_aa64.efi` |
  | `grub_efi_target`, `grub_bios_target` | `x86_64-efi`, `i386-pc` | `arm64-efi`, none |
  | `grub_efi_packages` | the old list, unchanged | `grub-efi-arm64-signed`, `grub-efi-arm64`, `grub-arm64-efi`, `grub2-efi-aa64` |
  | `grub_bios_packages` | `grub-pc`, `grub-i386-pc` | none |
  | `shim_packages` | `shim-signed`, `shim-x64`, `anduinos-shim-signed` | `shim-signed`, `shim-aa64` |
  | `last_resort_packages` | the old list | `grub-efi-arm64`, `systemd-boot` |

  - x86_64's row holds exactly the names bko-holt used before. Limine's
    binary has the fallback path's name. MokManager and shim's fallback
    (`mmaa64.efi`, `fbaa64.efi`) come back with the ESP, and no code names
    them, on either architecture.
  - **Readers.** On the dump side, `boot::detect_loader_on` takes a row:
    the fallback path, a fallback that is a copy of shim, the ESP's GRUB,
    systemd-boot and rEFInd files, and every package list. `detect_loader`
    is x86_64's. `boot_json` passes `arch::names_of(sys)`, the machine's
    row. `grub_json` (the `*-efi-signed` directory) and `proxmox_mode` read
    the row too.
  - On the load side, `commands.rs`'s `boot_names()` returns the dump's row
    (`arch::names_of_dump`). These read it: `grub-install --target`, the
    fallback path and its GRUB-copy check under `--reformat`, the store
    systems' images and `store_removable`, `bootloader_id`'s ESP scan, the
    AnduinOS note, Proxmox's systemd-boot entry, and ELILO's `--removable`.
    `zfsbootmenu.rs` reads its notes' fallback path from the row too.
  - `boot::PACKAGES` gained the ARM64 names (`grub-efi-arm64-bin`,
    `shim-helpers-arm64-signed`, `linux-image-arm64`,
    `linux-image-cloud-arm64`, `grub2-efi-aa64`, `shim-aa64`,
    `grub-arm64-efi`). No amd64 system has them, so no fingerprint changes.
    `testvm/update/fingerprint.py`'s PACKAGES, which test_vm_update holds
    to boot.rs's, gained the same names.
  - fingerprint.py's `LAYOUT_DROP` drops `firmware.efi_arch`, as Bazzite's
    B2 did for its new keys (`os.machine` is under `os.`, already dropped).
    Without that, the first vm-update dump with the A3 bko-holt would move
    every UEFI guest's layout digest and lose its carry. Checked on the
    corpus: all 38 UEFI x86 dumps keep their digest with both keys added.
  - **Left x86-only, by name.** These distros ship no ARM64 build that
    bko-holt supports:
    - TrueNAS (`load/truenas.rs`).
    - Solus's `bootloaderx64.efi`.
    - Ubuntu Core, whose load already refuses every model but amd64 `pc`.
    - LILO and ELILO's detection.
  - Every `i386-pc` path is BIOS, and load never reaches one for an ARM64
    dump (below). `bko-holt load --help` still says `BOOTX64.EFI`, so that
    x86_64's output stays the same.
- **dump records the architecture.**
  - `os.machine` is the kernel's name for the machine (`uname -m`):
    `/proc/sys/kernel/arch`, or else `uname -m`.
  - A tree other than `/` (`--sysroot`, the tests' fake systems) that says
    neither is `x86_64`, so the tests give the same results on any host.
  - `/` with neither is the binary's own architecture.
  - `os.architecture` is unchanged: the dumping binary's own
    (`std::env::consts::ARCH`).
  - On UEFI, `firmware.efi_arch` is UEFI's name for the firmware:
    - `x64`, or `ia32` when an x86_64 kernel runs on 32-bit firmware
      (`fw_platform_size` 32)
    - `aa64` on ARM64
  - A dump made before A3 has no `os.machine`. It counts as
    `os.architecture`'s machine, which is `x86_64` in every such dump, or
    as `x86_64` without either (`arch::dump_machine`).
  - `Arch::parse` also takes the BSDs' `amd64` and `arm64` and illumos's
    `i86pc` (64-bit only). Without that, a FreeBSD or OmniOS rescuer, whose
    `uname -m` it reads, would have been refused.
- **load refuses another architecture early.** `arch::check_load` runs in
  `load::run`, right after the layout is parsed, before any stage. Plan,
  status and a `--commit` run alike stop there with exit 3
  (`EXIT_UNSUPPORTED`). By then load has scanned no disk, run no command
  and written nothing: the target scan, mapping and every command come
  after it. It compares the dump's machine with the rescue system's and
  names both, for example: "the dump is of an ARM64 system (aarch64), and
  this rescue system is x86_64 (x86_64): load restores a system only from
  a rescue system of its own architecture; boot BackOtter's arm64 image
  (backotter-live-linux-VERSION-arm64.img) on the target machine".
  - It also refuses an architecture it doesn't know, on either side.
  - It refuses an ARM64 dump whose firmware mode isn't `uefi`: ARM64 has no
    BIOS path, and U-Boot and devicetree boot are out of scope.
- **The fake rescue systems** that plan a dump take the dump's own
  architecture (`/proc/sys/kernel/arch`): `bugreport::check_plan`, and
  golden_plans.rs's `common::plan_onto`. Without that, an ARM64 dump's
  check_plan and golden plan would be refusals.
- **Fixtures.** No ARM64 guest exists yet (A5), so the corpus has two
  fixtures, its first that aren't guests' dumps:
  `holt/tests/corpus/arm64-grub-shim` and `arm64-systemd-boot`, each with
  `src.json`, `plan.txt` and an index.tsv row (`fixture`, run `a3`). Each
  is the dump of a fake Debian 13 arm64 tree in arch.rs's tests:
  - **GRUB through shim.** Debian's arm64 shim, GRUB and MokManager in
    EFI/debian, shim as `BOOTAA64.EFI` with `fbaa64.efi`,
    `/boot/grub/arm64-efi`, grub-efi-arm64-signed, the ESP at /boot/efi, and
    shim's NVRAM entry booted. Its golden plan runs `grub-install
    --target=arm64-efi --efi-directory=/boot/efi --bootloader-id=debian
    --no-nvram` and `update-grub`, then makes the entry for
    `\EFI\debian\shimaa64.efi`.
  - **systemd-boot.** The ESP as /boot (the Arch and Fedora layout),
    `EFI/systemd/systemd-bootaa64.efi` and its copy at the fallback path,
    and its NVRAM entry booted. Its golden plan runs `bootctl install
    --no-variables`, then makes the entry for
    `\EFI\systemd\systemd-bootaa64.efi`.

  `arm64_corpus_fixtures_are_these_dumps` holds each src.json to its tree's
  dump, apart from three things: the tool's version, `os.architecture`
  (written as the aarch64 binary's), and the mounts' statvfs figures (the
  test host's, so they are left out). `BKO_HOLT_GOLDEN=bless` rewrites them.
  The corpus README says the same.
- **Tests.** arch.rs has 10 tests:
  - each row's names keep UEFI's pattern, and ARM64 has no BIOS
  - the machine: the kernel's, then `uname -m`, then the fake system's
    default
  - `efi_arch`
  - pre-A3 dumps: `x86_64`, loaded on an x86_64 rescuer and refused on an
    ARM64 one
  - the refusal both ways, and of unknown architectures and non-UEFI
    ARM64
  - both fixtures' dump and boot stage, with no x86 name in either
  - detection from the fallback path, from a copy of shim, from the ESP's
    files alone (GRUB, systemd-boot, rEFInd, Limine at EFI/BOOT or in
    EFI/limine) and from packages alone. The same files on x86_64 find
    nothing.
  - the refusal before any disk: plan, status and `--through close --commit
    --erase`, each direction, with a runner that records every command.
    Nothing ran, and the target disk's bytes are unchanged.
  - the corpus fixtures

  efi.rs's test checks `efi_arch` `x64`.

### Test results

On cinqcent, from `~/backotter-work/a3-scratch` (a copy of main d23c9ab
plus A3 only), `cargo test --offline --no-fail-fast`:

| suite | HEAD d23c9ab | HEAD + A3 |
|---|---|---|
| lib | 568 passed | 578 passed |
| golden_plans | 86 plans of 76 dumps, 2 differ | 88 plans of 78 dumps, 2 differ |
| other test binaries | all pass | the same |

- The 2 that differ in both columns are `ubuntu24-luks` and
  `ubuntu24-uefi`. classes.json has them as HoltTest2 sources, and the
  corpus has no dump of either, which was already so at HEAD.
- Every other plan matches its plan.txt byte for byte, so every x86_64
  golden plan is unchanged.
- On Chopin with Rust 1.94.1, `cargo check --all-targets --target
  aarch64-unknown-linux-musl` is clean. clippy shows no warning in A3's
  code.
- testvm/test2's unit tests (all 37 modules, on the Mac): 1803 run, and
  the same 37 failures and 2 errors as at HEAD d23c9ab, none of them A3's
  (test_vm_update's `test_packages_are_boot_rs` failed until
  fingerprint.py's PACKAGES had the ARM64 names).
- An x86_64 release build on cinqcent loaded `arm64-grub-shim/src.json`.
  It exited 3 with the refusal above, before anything else. debian13's
  dump got past the check. Its `dump` of cinqcent records `machine`
  `x86_64` and `efi_arch` `x64`.

### The Bigfoot build (2026-11-16)

Bigfoot was off the network on 2026-11-15, so this waited a day. It was
done from main `1552b54` (A3 plus the holt changes since), a `git archive`
of holt/ and the scripts it needs (build-holt.sh, hosts.sh, hosts.json,
hosts.py) in Bigfoot's `~/backotter-work/a3-scratch/bf1`. No source change
was needed.

- **The build.** `build-holt.sh linux-arm64`, whole in `bko.slice`, with
  `HOLT_SRC`, `HOLT_OUT`, `HOLT_LOGS`, `HOLT_INFO` and `TMPDIR` in the
  scratch directory. Rust 1.94.1 (`e408947bf`, host
  `aarch64-unknown-linux-gnu`, LLVM 21.1.8) from
  `/home/m/bko-arm64/toolchains/rustup`, target
  `aarch64-unknown-linux-gnu`, `+crt-static`, release, about 2 minutes.
  - SHA-256 `21d9cbfb21c225292049c851b3cdba6702339203c51185516bd10949b49eade2`,
    4,530,232 bytes: a static, stripped aarch64 ELF. `--version` says
    `bko-holt 0.2.6`.
  - Two builds, each with its own fresh target/ and tree copy at another
    path, are the same bytes.
- **The tests, natively.** `cargo test --offline --no-fail-fast` in
  `bko.slice`: 641 passed, 8 failed, 3 ignored. The lib's 600 all pass.
  - 7 are realtools.rs's, only because Bigfoot lacks the tools
    (cryptsetup, mkfs.btrfs, mkfs.jfs, sgdisk and others), and installing
    them would be a system change. Its binary, built there, run as root
    in the A0 guest with the tools installed: all 17 pass.
  - 1 is golden_plans: `vanilla3` differs by one line in assemble, the
    thin pool's note "a dump from before Qubes's Q2" (08f427f's). That
    isn't ARM64's: Chopin's x86_64 run at the same commit shows the same
    one difference (648 passed, that 1 failed). The two arm64 fixtures'
    plans match.
- **The guest.** The A0 Debian 13 arm64 genericcloud image on a fresh qcow2
  overlay, AAVMF without Secure Boot, port 3281, in `bko.slice`, with a
  blank 8 GiB virtio spare disk (vdc). The binary ran as root in the guest.
  - `dump --only-disk vda` records `os.machine` `aarch64` and
    `firmware.efi_arch` `aa64`. Boot is GRUB through shim
    (`grub-efi-arm64-signed`, `shim-signed`), found on the real ESP:
    `EFI/BOOT/BOOTAA64.EFI` is a copy of `EFI/debian/shimaa64.efi`, beside
    `grubaa64.efi`, `mmaa64.efi` and `fbaa64.efi`. GRUB has `arm64-efi`
    installed. No warnings.
  - The image's firmware booted the fallback path ("UEFI Misc Device"), so
    NVRAM had no Debian entry. Plan onto vdc (exit 0) runs `grub-install
    --target=arm64-efi --efi-directory=/boot/efi --bootloader-id=debian
    --no-nvram` and `update-grub`, and makes no entry. The fallback path
    comes back with the ESP.
  - Then, with a `debian` entry for `\EFI\debian\shimaa64.efi` made and
    booted, the dump's evidence names it. The plan then also runs
    `efi-dedupe` and `efibootmgr --create --disk /dev/vdc --part 15 --label
    debian --loader '\EFI\debian\shimaa64.efi'`. Neither plan names an x86
    file or target.
  - The refusal: the corpus's `debian13` (BIOS) and `ubuntu26-uefi` dumps,
    as plan, `--only status` and `--erase /dev/vdc --through mount
    --commit`. Each exited 3 with "the dump is of an x86_64 system
    (x86_64), and this rescue system is ARM64 (aarch64): load restores a
    system only from a rescue system of its own architecture; boot
    BackOtter's amd64 image (backotter-live-linux-VERSION-amd64.img) on the
    target machine". strace shows bko-holt's own execve and no other, and
    no open of /dev/vdc. vdc's write counters stayed 0, and it still reads
    as all zeros.
  - The guest was powered off, and its overlay and spare deleted.

Nothing is promoted into bko-bin or holt-bin. Bigfoot's
`holt-bin/linux-arm64` still holds A2's `908ea6ea...` build.

## What A4 built (2026-11-16)

- **The source.** `ubuntu-26.04.1-live-server-arm64.iso` (the 4 KiB-page
  one, not `+largemem`), from cdimage.ubuntu.com, its `SHA256SUMS` checked
  against the Ubuntu CD key (`843938DF...`), sha256 `af78753c...`. It is in
  Bigfoot's `/bkoscratch/imgbuild/server`, as the amd64 ISO is on the farm.
  Its pool has `zfsutils-linux` 2.4.1 for arm64, and archive.ubuntu.com
  serves `resolute`'s arm64 packages (no ports.ubuntu.com needed).
- **The scripts.** Every change is keyed by architecture, and amd64's path
  is what it was:
  - `slim-base-layer.sh` reads the architecture from the base
    (`dpkg --print-architecture` in its chroot; another host's base stops
    there). On arm64, tier 2 drops x86 SoCs' firmware instead of ARM's
    (below), and mksquashfs runs without `-Xbcj` (plain xz: 4.6.1 has no
    arm64 filter). `--settings ARCH` prints tier 2's lists and mksquashfs's
    options and exits. The report says `Arch: arm64` on arm64 only.
  - `build-backotter-layer.sh`: `BKO_ARCH=arm64` also sets the DKMS path's
    machine (`aarch64`) and plain xz. `--settings` prints what `BKO_ARCH`
    sets.
  - `make-img.sh` reads the architecture from the ISO's `.disk/info`. On
    arm64:
    - the serial entry is `console=tty0 console=ttyAMA0,115200n8` (SBSA's
      and QEMU virt's PL011)
    - the stock menu's `$cmdline` block (its Snapdragon X workarounds) is
      kept, and the entries pass `$cmdline`
    - the firmware entries are taken from "Boot from next volume" on, since
      the arm64 menu has no `grub_platform` test
    - `-boot_image any partition_offset=16`. The stock arm64 ISO has offset
      0, so partition 1 holds no ISO 9660 tree, and casper mounts the
      whole stick on /cdrom. While it does, the kernel won't let anything
      mount the config partition (sda3: "Can't open blockdev"), and
      `config-rw` and `config-settings` failed. amd64's ISO already has
      offset 16, which puts /cdrom on sda1. With it, arm64's /cdrom is sda1
      too.
  - `boot-test.py --arch amd64|arm64` (default: the host's). arm64 runs
    `qemu-system-aarch64 -machine virt,gic-version=host -cpu host
    -enable-kvm`, with AAVMF in pflash (code read-only, a copy of the
    vars): `no-secboot` code and `AAVMF_VARS.fd` for `uefi`, the `ms` pair
    for `uefi-secureboot`. The NIC is `virtio-net-pci,romfile=`, the stick
    is on qemu-xhci, and the CD is a `scsi-cd` on virtio-scsi (virt has no
    IDE). `bios` and non-Linux platforms are refused.
  - `linux/checks.sh` lines may start `[amd64] ` or `[arm64] `.
    boot-test.py types such a line, without the tag, only for that
    architecture. arm64's checks are `llvm-gone` (aarch64's path),
    `x86-firmware-gone` and `arm-firmware-kept`, in place of amd64's
    `llvm-gone` and `arm-firmware-gone`. amd64's typed script is the same
    bytes as before.
- **Firmware.** Ubuntu's arm64 kernel builds none of the drivers for these,
  and none of its 8,542 modules lists any of these files (`modinfo -F
  firmware`). So arm64's tier 2 drops them (700 files, 55.7 MB unpacked):
  - Intel SoC audio DSPs: `intel/sof*`, `intel/avs`, `intel/catpt`,
    `intel/dsp_fw_*`, `intel/fw_sst_*`, `IntcSST2`
  - Intel's sensor hub: `intel/ish` and `LENOVO/`, `HP/`, `dell/`'s `ish/`
  - Intel's IPU cameras: `intel/ipu`, `ipu3-fw`, `irci_*`
  - the NPUs: `intel/vpu`, `amdnpu`

  Everything else stays:
  - the ARM firmware amd64's image drops: `qcom` (171 MB), `nvidia/tegra*`,
    MediaTek, i.MX, Mali and the rest
  - the PCIe and USB parts' firmware: iwlwifi, `intel/ibt`, `intel/ice`,
    `intel/qat`, `intel/vsc` (mei-vsc is built), `i915/` (xe asks for 27 of
    its files), `xe/`, amdgpu, radeon and nouveau
  - the modem cards, and the kernel's device trees (100 MB unpacked, 4.7 MB
    as xz)

  Tier 3 (switch ASICs) is unchanged: 36 files, 90.3 MB.
- **bcachefs: in.** apt.bcachefs.org's `resolute` lists arm64, with
  `bcachefs-tools` and `bcachefs-kernel-dkms` 1:1.39.7, the pinned version.
  DKMS built the module for 7.0.0-43-generic under `.../aarch64`: 1,456 KB
  stripped. It loads with Secure Boot off, and the kernel refuses it with
  Secure Boot on, as on amd64.
- **The build.** The slim base and the layer were built natively on Bigfoot,
  as root and whole in `bko.slice`, from a private tree in
  `~/backotter-work/a4-scratch/tree` (`git archive` of main `1552b54` plus
  A4's diff, `.backotter-commit` `1552b54.dirty`). They took about 6 and 3
  minutes. No mount or loop device was left. The binaries:
  `bko-bin/linux-arm64`'s restic-bko and rustic-bko (A2), and A3's bko-holt
  `21d9cbfb...` from a private `a4-scratch/holt-bin/linux-arm64`. Nothing
  was promoted.
  - The slim base: kernel 7.0.0-30 → 7.0.0-43-generic, 131 packages
    updated, 443 left. 1,593.7 MB unpacked, 903.3 MB as squashfs.
  - The layer: 157.5 MB unpacked, 77.9 MB as squashfs.
  - **`make-img.sh` ran on Chopin**, not Bigfoot. marker.py's config
    partition needs mtools (`mmd`, `mcopy`). Bigfoot lacks it, and it
    isn't in A0's package set. make-img.sh runs no chroot and no arm64
    binary, so its inputs (checked by sha256 at both ends) went to Chopin's
    `/bkoscratch/a4-scratch`, the images came back, and that directory was
    deleted. No batch was running. With `apt install mtools` on Bigfoot,
    the whole build would run there.
- **The image.** `backotter-live-linux-dev.20261116.1552b54.dirty-arm64.img`,
  in Bigfoot's `~/backotter-work/a4-scratch/images/out/`:
  1,420,623,872 bytes, sha256
  `5f64f934f478fd38857bcbc73e7e0c31e5cc284da5727c030b0c8e862521de0b`. Its
  `--test` twin in `out/test/` is `3d60a778...`.
- **Image size.** amd64's current image
  (`dev.20261112.5b2fe9d-amd64`) is 1,248,854,016 bytes, so arm64's is
  171.8 MB (13.8 %) bigger:

  | part | amd64 | arm64 |
  |---|---|---|
  | slim base squashfs | 753.2 MB | 903.3 MB |
  | layer squashfs | 68.7 MB | 77.9 MB |
  | vmlinuz | 16.9 MB | 23.3 MB |

  Most of the base's extra 150 MB is ARM firmware, which arm64 keeps and
  amd64 drops (171 MB, against arm64's 56 MB of x86 firmware dropped), and
  plain xz in place of xz with BCJ.
- **Boot tests.** boot-test.py's checks, on Bigfoot in `bko.slice` (its
  CPUs are all A720s, so QEMU needs no taskset):

  | mode | media | reached the shell | checks |
  |---|---|---|---|
  | `uefi` | USB stick | 28 s | 36 ok, 0 FAIL |
  | `uefi-secureboot` | USB stick | 28 s | 36 ok, 0 FAIL (shim, GRUB and the kernel signed; "Secure Boot enabled") |
  | `uefi` | CD | 25 s | 33 ok, 0 FAIL |

  - **Versions:** kernel 7.0.0-43-generic, `restic 0.19.1+backotter.7
    compiled with go1.25.10 on linux/arm64`, `rustic 0.11.4+backotter.13`,
    `bko-holt 0.2.6`, ZFS `zfs-2.4.1-1ubuntu5.1` (kmod the same), bcachefs
    1.39.7, Python 3.14.4. The three binaries' sha256s in the image match
    the sources' (`b79fe55a...`, `bdfbda6d...`, `21d9cbfb...`).
  - The marker reads `linux ubuntu 26.04.1 zfs 2.4.1 47 features`.
  - `--manifest` wrote 39 fields.
- **In the image** (a one-off driver on boot-test.py's console, with a blank
  8 GiB virtio spare as vda):
  - `modprobe zfs` loaded zfs and spl, and a pool on a file was created,
    listed and exported.
  - `bko-holt dump` of the rescue system exited 0: `os.machine` `aarch64`,
    `firmware.mode` `uefi`, `efi_arch` `aa64`, no warnings. `dump
    --only-disk vda` exited 0 too.
  - Then the corpus's `arm64-grub-shim` dump was typed in (sha256
    matching), and its plan was made: `load src.json --map vda=/dev/vda
    --erase /dev/vda`. It exited 0, with `grub-install --target=arm64-efi
    --efi-directory=/boot/efi --bootloader-id=debian --no-nvram` and no x86
    name. vda's write counter stayed 0, and its first 64 MiB still read
    as zeros.
- **amd64 unchanged.**
  - boot-test.py's QEMU command is the same for all 42 cases (7
    platforms, 3 modes, 2 media), and its typed checks are the same bytes
    for every platform and medium.
  - `--settings amd64` gives the old tier 2 lists and mksquashfs options in
    both build scripts, and the DKMS machine is `x86_64`.
  - HEAD's and A4's `make-img.sh`, run on the same fake amd64 ISO, give
    the same file tree, the same `md5sum.txt` and the same boot layout,
    apart from the marker's random IDs and xorriso's timestamp, with and
    without `--test`. The amd64 image itself wasn't rebuilt.
- **Tests.** `testvm/test2/tests/test_arm64_image.py` (17 tests) covers:
  - boot-test.py's amd64 commands as before, arm64's (USB, CD, Secure
    Boot), the refusals and the default architecture
  - the `[ARCH]` filter
  - both scripts' `--settings` for each architecture
  - make-img.sh's menu and layout against a fake ISO. These 3 tests need
    Linux's xorriso and mksquashfs; on the Mac they are skipped.

  It passes on the Mac and on Bigfoot. test_img and test_arm64_builds pass.
  test_plan has the same 17 failures at `1552b54` as with A4.

Left for later:
- t2 and img-one.sh don't build or boot-test the arm64 image yet. Their
  bins are `bko-bin/linux` and `holt-bin/linux`, and their boot modes
  include `bios`. That waits for A6 and a promoted `linux-arm64` set.
- The image isn't published or promoted anywhere.
- HoltTest1 1ARM's `+img` round trip is A6's.

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
