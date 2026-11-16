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

## What A5 built (2026-11-16)

- **The builders.** Each takes `--arch amd64|arm64`, amd64 by default, and
  amd64's path is what it was. On arm64 the builder:
  - refuses to run on a host that isn't aarch64
  - refuses a guest name without `-arm64`, since hosts.py reads a guest's
    architecture from its name
  - installs through **vm-run**, not its own `qemu-system-x86_64` line.
    vm-run already has virt, AAVMF, the SCSI CD, the pinning and the
    `job_slice` (A1).
  - always installs with AAVMF's `no-secboot` code on a fresh variable
    store
  - puts every console on `ttyAMA0`, virt's PL011

  | guest | builder | from | disk | vm-run-options |
  |---|---|---|---|---|
  | `debian13-arm64` | `make-debian.sh --arch arm64 13.0.0 ext4` | `debian-13.0.0-arm64-DVD-1.iso`, offline as amd64's | GPT: ESP 512 MB, `/boot` ext4, swap, `/` ext4; GRUB in `EFI/debian` and `EFI/BOOT/BOOTAA64.EFI` (`force-efi-extra-removable`) | `--uefi --no-secure-boot` |
  | `ubuntu26-arm64` | `make-ubuntu.sh --arch arm64 --uefi --name ubuntu26-arm64 26.04.1` | `ubuntu-26.04.1-live-server-arm64.iso` (cdimage.ubuntu.com, A4's copy hard-linked in), no network | Subiquity's `direct`: ESP, ext4 `/`, swap file; shim + GRUB | `--uefi` (Secure Boot, AAVMF's `ms` pair: Decision 10) |
  | `fedora44-arm64` | `make-fedora.sh --arch arm64 --uefi --name fedora44-arm64 44-1.7` | `Fedora-Server-dvd-aarch64-44-1.7.iso` | Anaconda's btrfs autopart (`storage-uefi.ks`): ESP, XFS `/boot`, btrfs `root`, BLS; shim + GRUB | `--uefi --no-secure-boot` |
  | `alpine324-arm64` | `make-bootstrap.sh --arch arm64 alpine` | Alpine 3.24.3's aarch64 minirootfs | alpine-uefi's (ESP at `/boot/efi`, swap, `/`) with `/` on XFS, as alpine324's | `--uefi --no-secure-boot` |
  | `debian13-zfs-arm64` | `sudo make-debian-zfs.sh --arch arm64` | debootstrap trixie, Bigfoot's OpenZFS pins `openzfs-2.3-linux` | GPT: ESP, `/boot` ext4, swap, `rpool` (vda4); `grub-efi-arm64` with `--force-extra-removable --no-nvram` | `--uefi --no-secure-boot` |

  Per-builder notes:
  - **Debian.** The preseed's MBR recipe is replaced with a GPT one that
    puts an EFI partition first, and the kernel is `linux-image-arm64`.
    Devuan and Kali are refused on arm64. The host name is `debarmN`
    (`debarm13`), not `debN-arm64`: that is Debian's arm64 kernel flavour
    (`6.12.38+deb13-arm64`), in the dump's uname and `/boot` paths, so
    bko-holt's redaction couldn't take the host name out
    (`check_plan`: "left in the redacted dump: [hostname]"), and A6's
    `t2:corpus` made no golden. Bigfoot's image, installed as
    `deb13-arm64`, is renamed in place by
    `testvm/arm64/rename-guest-host.sh` (A6, "Left for later").
  - **Ubuntu.** Only the `direct` layout; the other layouts are refused.
    The CIDATA disc is a second SCSI CD on its own controller, and
    `-nic none` comes through `--qemu-options`.
    - 26.04's arm64 `ubuntu.sources` names archive.ubuntu.com, not
      ports, so the snapshot freeze and vm-update's unfreeze work
      unchanged. Checked in the ISO's squashfs, and the `--try` below
      ran the unfreeze step.
    - The ISO is fetched from `releases/VERSION/release` first, then
      `releases/RELEASE/release`.
  - **Fedora.** The kickstart's `ttyS0` becomes `ttyAMA0`, and `-mcelog` is
    dropped, since aarch64 has no mcelog package. `--raid` and `--ostree`
    are refused.
  - **Alpine.** Arch has no aarch64 ISO, so the live system is Alpine's
    own `alpine-virt-3.24.3-aarch64.iso`, checked against Alpine's key.
    - It boots its kernel with `ip=dhcp apkovl=http://10.0.2.2:PORT/bko.apkovl.tar.gz`.
      The overlay holds `.default_boot_services`, the network, and a
      local.d script.
    - The local.d script adds bash, util-linux's tools, xfsprogs and
      dosfstools from dl-cdn, then runs the same `install.sh` the
      amd64 path uses.
    - `arch-install-scripts` can't be installed there: its `genfstab`
      conflicts with alpine-conf's. So the script writes a small
      `arch-chroot` of its own, and `genfstab -U` is Alpine's. Alpine's
      lists no swap, so alpine.sh adds the swap line on arm64 and checks
      that `/` is by UUID.
    - `bootstrap/common.sh` gains `ARCH` and `SERIAL_TTY`. On amd64 they
      are `amd64` and `ttyS0`, the same as before, and the pacman-init
      wait runs only on amd64.
    - `alpine.sh` takes GRUB's target (`arm64-efi`) and the getty's TTY
      from these.
  - **debian13-zfs.** It still runs as root on the host, in `bko.slice`.
    - The chroot gets `/sys` by a plain bind, so Bigfoot's efivarfs isn't
      in it. debconf sets `grub2/update_nvram false`, and grub-install
      runs with `--no-nvram`. Bigfoot's NVRAM is never reachable.
    - The first boot goes through vm-run.
    - When the module build fails, its `make.log` is kept as
      `dkms-make.log`.
- **vm-run** needed no change.
- **vm-update.** `dump_plat` names the bin folder a guest's dump takes its
  bko-holt from. For a `*-arm64` guest that is `linux-arm64/`, beside
  `linux`'s top. Everything else in vm-update already worked on arm64:
  - boot, update and reboot go through vm-run
  - the host mount goes through vm-mount
  - `--hosts` already kept to the guest's architecture (A1)
- **vm-chroot** refuses a guest whose `/bin/sh`, followed inside the
  guest, is an ELF of another machine than the host's: an arm64 guest on
  an x86_64 host, or the reverse. A guest with no `/bin/sh`, or one that
  isn't ELF, isn't refused.
- **vm-umount** had a bug that existed before A5 and only shows on Bigfoot,
  which has no btrfs-progs. It ran `btrfs device scan --forget` on a btrfs
  guest's partitions without checking the command exists. Under `set -e`
  that exited 127 after the unmount and before the nbd detach, which left
  `fedora44-arm64`'s nbd device and state file behind. It now skips the
  forget when `btrfs` is missing, as vm-mount does for its scan.
- **Where the guests are.** Bigfoot's `~/backotter-work/vms/GUEST`
  (`/home/m/bko-arm64/work/vms`), with the ISOs beside them. They are not
  in vm-store (Decision 9), and no `store.json` exists on Bigfoot.
  rebuild-one.sh has no arm64 recipes: its boot check is compare.py's,
  which is still x86-only (A1). The builder lines in the table are the
  recipes.

### Checks on Bigfoot

Every guest was built by its builder, as `m` (debian13-zfs as root), in
`bko.slice`, from a private tree in `~/backotter-work/a5-scratch/tree`.
`check.sh` booted each once with its `vm-run-options` and `--snapshot`,
then ran `vm-mount --ro`, `vm-chroot`, `vm-umount`. After that,
`vm-update --try --hosts ''` ran from the same tree with
`BKO_WORK=~/backotter-work`.

| guest | built | first SSH | what the guest said | vm-mount `--ro` | vm-chroot | `vm-update --try` |
|---|---|---|---|---|---|---|
| `ubuntu26-arm64` | 3.9 GB | 18 s | 7.0.0-30-generic aarch64, "Secure boot enabled", kernel locked down | ext4 `/`, vfat `/boot/efi` | aarch64, `EFI/BOOT/BOOTAA64.EFI` + `mmaa64`/`fbaa64` | ok, 11 min: 181 packages and the unfreeze, rebooted on 7.0.0-43, mounts 2 file systems |
| `debian13-arm64` | 1.2 GB | 25 s | 6.12.38+deb13-arm64, Secure Boot off | ext4 `/`, ext4 `/boot`, vfat `/boot/efi` | aarch64, `BOOTAA64.EFI` + `grubaa64` | ok, 6 min: 119 packages and the unfreeze, rebooted on 6.12.117, mounts 3 |
| `fedora44-arm64` | 3.2 GB | 26 s | 6.19.10-300.fc44.aarch64, `rootflags=subvol=root` | btrfs `[/root]`, xfs `/boot`, vfat `/boot/efi` | aarch64, `BOOTAA64.EFI` + `fbaa64` | ok, 23 min: 348 packages and the unfreeze (updates repository on), rebooted on 7.3.3-200.fc44, mounts 3 |
| `alpine324-arm64` | 0.3 GB | 12 s | 6.18.63-0-lts, XFS `/` by UUID | xfs `/`, vfat `/boot/efi` | aarch64, `BOOTAA64.EFI` | ok, 1 min: 4 packages, mounts 2 |
| `debian13-zfs-arm64` | 1.8 GB | 26 s | 6.12.117+deb13-arm64, `root=ZFS=rpool/ROOT/debian`, rpool ONLINE, zfs 2.3.9 (userland and kmod) | rpool's 9 datasets under a temporary name, ext4 `/boot`, vfat `/boot/efi` | aarch64, `BOOTAA64.EFI` + `grubaa64` | ok, 2 min: 8 packages, mounts 11 |

What the boots showed:
- Each guest saw 2 vCPUs, all part 0xd81 (A720s), inside the slice.
- Each disk's first SSH came with a fresh variable store, through the
  fallback path.
- No nbd device, mount, loop device or `bkodz` pool was left behind.
- DantePool stayed `ONLINE` throughout.

Two failures came up along the way, and both are fixed:
- **alpine324-arm64, first try.** The live system's `apk add` refused
  `arch-install-scripts`, because its genfstab conflicts with alpine-conf's.
  The local.d script stopped with BACKOTTER-INSTALL-FAILED and powered off
  at once. Its own arch-chroot (above) fixed it.
- **debian13-zfs-arm64, first try.** zfs-dkms's module build failed
  ("bad exit status: 2"). Four builds ran in the slice at once then. The
  cleanup deleted the chroot and the module's `make.log` with it, so the
  cause is unknown. The second try passed unchanged, alone but for one
  `--try`. The builder now keeps `dkms-make.log`.

Other things seen:
- **debian13-zfs's folder was root's.** The root builder makes WORK_DIR as
  root when it doesn't exist, which it always did, so `check.sh`'s boot as
  `m` couldn't write its serial log there. It was chowned by hand.
  rebuild-one.sh makes the folder as `m` first.
- **Disk use on Bigfoot's `/home`:** 53 GB before A5, 70 GB after. That is
  21 GB of guest folders (the ISOs included; Ubuntu's 3.1 GB ISO is a hard
  link to A4's) and 19 MB of `a5-scratch`.

### Tests

`testvm/test2/tests/test_arm64_guests.py` (19 tests):
- **The builders.** Each runs in a temporary tree with every outside
  program stubbed: wget, gpg, xorriso, qemu-img, QEMU, vm-run and the rest.
  - amd64's QEMU command lines for make-debian, make-ubuntu (BIOS and
    `--uefi`), make-fedora and make-bootstrap are the ones HEAD's builders
    give. That was checked by running the same tests against HEAD's
    scripts.
  - Each builder's arm64 vm-run line, downloads, preseed, user-data,
    kickstart, install script and apkovl (`sh -n` on its local.d script),
    and its vm-run-options.
  - The refusals, including make-debian-zfs.sh's (it needs root, so only
    those are tested).
- **vm-update.** `dump_plat`, and which bko-holt `dump` copies.
- **vm-chroot.** Same-architecture guests run, with `/bin` as a link or a
  directory. Foreign ones are refused. No `/bin/sh` passes.

Results:
- **Bigfoot:** 19 run, 0 failures.
- **Mac:** 2 run, 17 skipped (bash 3.2, no GNU timeout).
- **test_vm_update, test_arch and test_vm_run on the Mac:** the same 3
  failures and 1 error as main, none from A5.

Left for later:
- A6: SticTest1 entries, HoltTest1 1ARM and the Test2 registrations for the
  five.
- No `vm-update` real update (merge) was run. The guests are at their
  builders' fresh-install snapshots.
- make-debian-zfs.sh still mounts its new system at `/mnt/bko-debian-zfs-PID`
  while it builds, outside `/home/m/bko-arm64`. The cleanup trap removes it.

## What A6 built (2026-11-16)

A6's heavy testing is one command on the Mac, `testvm/arm64/a6-run.sh`,
which runs every stage on Bigfoot by itself. It needs the plumbing below,
which is built and unit-tested. The heavy run itself is the user's to
start.

### How to run it

From the checkout's top on the Mac:

```
testvm/arm64/a6-run.sh --dry-run        # the plan, from a scratch copy on Bigfoot; runs nothing
testvm/arm64/a6-run.sh                  # start it all: 2.5 to 4 h est.
testvm/arm64/a6-run.sh --status         # the latest run (the last started): state, stages so far, the log's tail
testvm/arm64/a6-run.sh --fetch          # its logs and summary to ~/backotter-work/a6-run/STAMP/
testvm/arm64/a6-run.sh --stop           # stop it: the stage cleans up, the rest is ABORTED
```

- **Flags.** `--only S[,S]` and `--skip S[,S]` take stage names and the
  groups `stic`, `1ARM`, `img`, `hosttest2` and `t2`. Preflight and
  final always run. A holt stage brings `rescuer` along, and an `+img`
  stage brings `imgpin`, unless `--skip` names them.
  - `--bins DIR` tests another set of binaries (absolute, `~/...`, or
    relative to `~/backotter-work`).
  - `--img FILE` uses another `--test` image.
  - `--img-guests G,G` changes the `+img` panel.
  - `--rebuild-rescuer` makes the rescuer again.
  - `--from-run STAMP` gives `t2:corpus` run STAMP's 1ARM results (its
    `out/holt` reports and `/bkoscratch/holttest1d/a6-STAMP`) instead of
    this run's, for a rerun of failed stages with no holt stage in it
    (`--only t2:corpus,...`; with one it is a usage error). STAMP must be
    a run on Bigfoot with `out/holt`.
  - `--stamp NAME` names the run (default `YYYYMMDD-HHMMSS`).
  - `--tree DIR` ships DIR's files instead of HEAD, for testing the driver.
  - `--host USER@HOST` (default the bigfoot row's `m@192.168.68.197`).
  - `--no-batch-check`, `--list`, `--help`.
  - A value-taking flag with no value is a usage error (exit 2).
  - `--status`, `--fetch` and `--stop` without a STAMP take the run last
    started (`~/backotter-work/a6-run/.latest`, written at each start),
    else the newest run directory by mtime, never the last by name.
- **What it ships.** A clean copy of committed HEAD (`git archive`), never
  the working tree, to Bigfoot's `~/backotter-work/a6-run/STAMP/tree`, with
  HEAD's commit in `tree/COMMIT`. Commit first: uncommitted A6 changes
  aren't in the copy.
- **Before it starts.** Bigfoot must answer and be aarch64, no `bko-a6-*`
  unit may be active, and STAMP must be new. It prints `t2 batch status`
  and refuses if a batch names bigfoot (none can: Bigfoot is no default
  host).
- **How it detaches.** It starts `testvm/arm64/a6-stages.sh` as a
  transient system service, `bko-a6-STAMP.service`:
  `sudo -n systemd-run --unit=bko-a6-STAMP --slice=bko.slice --collect --
  setpriv --reuid=m --regid=m --init-groups --reset-env -- bash ...`.
  - This is the same slice and setpriv as hosts.py's `job_prefix` scopes.
  - It is a system service, not a `--user` unit, because Bigfoot's user
    manager can't set the slice's AllowedCPUs or IOWeight (A0).
  - The Mac's terminal can close and the laptop can sleep. The command
    returns once the unit is running.
  - vm-run finds itself in bko.slice and doesn't start again.
    cmp-run.sh's scopes go in the same slice.

### The stages

They run in this order, one at a time. A failed stage doesn't stop the run,
except preflight.

| stage | what | est. |
|---|---|---|
| `preflight` | Checks Bigfoot: aarch64, `/dev/kvm`, `sudo -n`, the RAM disk, 80 GB free on /home, cmp-run.sh's ceiling, and no QEMU of ours running. Reads DantePool's health (`zpool list`, read only). Records the sha256 of the five guests, A4's `--test` image and the binaries in `out/inputs.txt`. Assembles `RUN/bins` (below). | 10 s |
| `stic:GUEST` ×5 | SticTest1's `check.sh` of each guest: both archivers, all four directions, the restore over immutable files, the non-root run, node identity. `STC_LOGS=RUN/out/stic/GUEST`, `BKO_TESTVM=` the shipped tree, port 3290. | 3-15 min each, 35 min |
| `rescuer` | `make-rescuer.sh --arch arm64`, run once; the image is kept after that. | 15-25 min, first time |
| `holt:GUEST` ×5 (1ARM) | `ht1.sh GUEST a6-STAMP` under `cmp-run.sh --reserve D M`, with D and M from ram-size.sh. ubuntu26-arm64's D 10 is over the 9.0 ceiling, so it runs on /bkoscratch (`HT_RAM=0`, `--reserve 0 M`), as `holt_disk_mode` does. | 3-8 min each, 30 min |
| `imgpin` | Pins A4's `--test` image in `RUN/t2/img-pin/linux-arm64`: a hard link (the image isn't written), its sha256, and its own three binaries, each checked against the image manifest's sha256. | 1 min |
| `holt:GUEST+img` | 1ARM `+img`, debian13-arm64 and debian13-zfs-arm64 by default: the round trip with the image as rescuer (`HT_VARIANT=img`, `T2_HOST_ROOT=RUN/t2`), in the RAM disk only. | 10 min each |
| `hosttest2:bin` | `gate/bincheck.sh static` on linux-arm64's archivers; `version --set RUN/bins --expect RUN/expect/expect.tsv`; `version --holt-version` (holt/Cargo.toml's) on bko-holt. Judged by `testvm/arm64/a6-bin.py judge` with t2lib/gate_judge.py's `bin_outcome` and `version_unchecked`: an unchecked binary or a SKIP FAILs, as in gate:bin:version. a6-run.sh makes `expect.tsv` on the Mac (`a6-bin.py expect`), as gate.py's `expected_versions` does: each fork's version line at the set's commits in BKO_GIT (default `~/git`); the judge checks they are the set's commits. With no expectations (a commit not in BKO_GIT) the stage FAILs. | 1 min |
| `hosttest2:loop-linux`, `:loop-sparse` | `gate/loop.sh linux` and `sparse` under `--reserve 8.5 4` and `6 4` (the small class's `host_reserve`), through run-all.sh's new arm64 profile. Its sparse-rt.sh keeps its lock and `out/` in the loop's log (`SPARSE_DISK_WORK`, new, arm64 profile only), not `/var/tmp`. After the loop, its empty mount points (`/mnt/PREFIX-*`) go; anything left there or in `/var/tmp` FAILs the stage. | 20-30 min each |
| `hosttest2:facts` | `t2-host facts` and `t2-host self-test`, with `T2_HOST_ROOT=RUN/t2`. | 1 min |
| `t2:corpus` | For each 1ARM PASS, the Test2 golden bless (below). | 10 min |
| `t2:limits` | Each check's LIMIT needs against `stic/limits.json` (`out/t2/limits-candidates.json`), and the passing stages' times as `estimates.tsv` rows (`estimates-candidates.tsv`). | seconds |
| `final` | The guests' and the image's sha256 again, which must be unchanged. Checks that no QEMU, pool or mount of the run is left. | 10 s |

**The binaries** (`RUN/bins`, laid out as a round: `linux-arm64/` with its
own `SHA256SUMS`, and a top `SHA256SUMS` of `./linux-arm64/NAME` lines):

- The archivers come from Bigfoot's `bko-bin/linux-arm64` (A2's
  `b79fe55a...` and `bdfbda6d...`).
- bko-holt is the newest native A3+ build: an AArch64 ELF holding A3's
  refusal text. Today that is `a4-scratch/holt-bin/linux-arm64/bko-holt`,
  `21d9cbfb...`, the same bytes as `a3-scratch/bf1/b1/out`. holt-bin's
  `908ea6ea...` is A2's, from before A3, and isn't picked.
- `--bins DIR` replaces the default.
- Nothing is promoted.

The SticTest1 checks take `RUN/bins/linux-arm64`, ht1.sh `HT_BKO_BIN` and
`HT_HOLT_BIN=RUN/bins`, and the loops `--bin RUN/bins`.

**Where it writes.** On Bigfoot, everything is in `RUN =
~/backotter-work/a6-run/STAMP` (`/home/m/bko-arm64/work/a6-run/STAMP`):

- `runner.log`
- `summary.tsv` and `summary.txt`: a row per stage with PASS, FAIL, SKIP
  or ABORTED, rc, times and a detail
- `state` (running, done) and `exit` (0, or 1 when a stage failed)
- `logs/STAGE.log`
- `out/`: each check's logs, the 1ARM reports and logs (not their
  dumps), HostTest2's results and the Test2 candidates
- `bins/`
- `t2/`: the pin, and the raw dumps (mode 0700)

HoltTest1's own records are in `/bkoscratch/holttest1d/a6-STAMP/`. `--fetch`
copies `runner.log`, `summary.*`, `state`, `exit`, `logs/` and `out/` to the
Mac's `~/backotter-work/a6-run/STAMP/` (rsync, no `--delete`). That is beside
SticTest1's and t2's Mac results, outside the repository. It never copies
`t2/` (the raw dumps stay on Bigfoot, as collect-dumps.sh requires),
`bins/` or `tree/`.

**The rules it keeps.**

- One heavy stage at a time. SticTest1 could take two slots there, but this
  is the user's daily machine.
- Every heavy command runs under `~/backotter-work/cmp-run.sh --reserve`,
  the RAM disk's shared ledger, with `WEAR_TAG` `a6:STAMP:STAGE` (ht1.sh's
  `ht1:a6-STAMP:GUEST`).
- RAM work goes in `/mnt/bko-ram`. debian13-zfs-arm64's SticTest1 overlay
  (about 12 GiB) and ubuntu26-arm64's 1ARM run are the exceptions: they go
  on /bkoscratch.
- Only `/home/m/bko-arm64` is written. `/bt`, BkoStore and DantePool are
  never read or written: DantePool's health is only listed.
  `holt:debian13-zfs-arm64` and the HostTest2 loops run only when
  `zpool list` says ONLINE, or says plainly "no such pool". A `zpool list`
  that hangs past 20 s (a suspended pool), any other health and any other
  error SKIP them, and preflight says why.
- No package, no system setting, and no loop device on the host. The
  loops' file systems are ZFS on files and tmpfs; the checks' loop file
  systems are inside the guests.
- **Stopping and cleanup.** A stage runs in a session of its own.
  `--stop` sends TERM to the runner (its `runner.pid`, checked to be
  a6-stages.sh), and the runner sends it to the stage's process group and
  nothing else. The tools in the group stop their own VMs, nbd devices,
  mounts and pools by their own traps (vm-lib.sh's `vm_halt`, ht1.sh's
  `ht_release`, run-all.sh's pool cleanup). make-rescuer.sh's arm64 VM is
  setsid'd out of every stage's tree, so make-rescuer.sh `--arch arm64`
  traps EXIT and TERM: however it ends (no SSH, a failed install, a stop),
  it stops its QEMU by its pid file, the PID checked against its command
  line (amd64's make-rescuer.sh is as before). On a stop during the
  rescuer stage the runner also TERMs that QEMU if it is still up, and
  `final` FAILs if it is.
  After 120 s the group gets KILL. Then the runner TERMs, and after 60 s
  KILLs, every process the stage started that is still running: the PIDs
  it read from /proc's parent links at the stop, such as vm_boot's
  setsid'd vm-run and QEMU. It also removes the run's own scratch,
  `/mnt/bko-ram/PREFIX-*` and `/bkoscratch/st1-scratch/PREFIX-*`. `final`
  still runs, read only, and names anything left behind.
- No pkill or killall.
- A lock (`~/backotter-work/a6-run/.lock`) keeps two runs apart.

### The plumbing

- **SticTest1: five checks**, `testvm/stictest1/GUEST/check.sh`. Each has
  its in-guest part from the amd64 guest with the same layout:

  | check | in-guest part |
  |---|---|
  | `debian13-arm64` | `../ubuntu26-uefi` (ext4 /, ext4 /boot, vfat ESP, ext4 project/casefold) |
  | `ubuntu26-arm64` | `../ubuntu26-uefi` |
  | `fedora44-arm64` | `../fedora44-uefi` (btrfs, XFS, vfat, a spare disk) |
  | `alpine324-arm64` | `../alpine324/check-inner.sh` |
  | `debian13-zfs-arm64` | debian13-zfs's, with `../debian13-zfs/real.sh` |

  debian13's own check makes a btrfs, which is its root, not
  debian13-arm64's. Each check:
  - refuses a host that isn't aarch64
  - FAILs if the guest doesn't say `aarch64`
  - takes `roundN/bin/linux-arm64` (drive.py's `arch_bin_os`)
  - stays within Bigfoot's 9.0 GiB ceiling

  Per-guest details:
  - **debian13-zfs-arm64** runs `--reserve 1 5`, with its overlay on
    `/bkoscratch/st1-scratch` (`ST1_SCRATCH`).
  - **debian13-arm64 and ubuntu26-arm64** install python3, dosfstools or
    e2fsprogs from the distribution if the image lacks them.
  - **alpine324-arm64** loads ext4 and loop before its fixture. The guest
    has no ext4 of its own, and without the module busybox's mount of the
    loop ext4 fails with EINVAL. The first smoke run caught this: the
    fixture landed on the root XFS, and atime FAILed in all four
    directions.

  drive.py:
  - A round that names no guests leaves out those of an architecture no
    `--hosts` host has. Named, one is refused, as before.
  - Its self-test expects `bin/linux-arm64` for `-arm64` guests.
  - README.md has the five estimates (the siblings' until measured).
- **HoltTest1 1ARM.**
  - **ht1.sh** reads `GARCH` and `PLAT` from the guest's name: `linux-arm64`
    for `*-arm64`, else `linux`.
    - It refuses a guest of another architecture than the host's (exit 2).
    - bko-holt and rustic-bko come from `PLAT/` of their sets, and the
      `+img` pin from `img-pin/PLAT`.
    - An arm64 guest's rescuer is `rescuer-arm64.qcow2` (ubuntu26-arm64,
      `--uefi`). It needs no bin/ debs, which are amd64's, so they are
      neither checked nor copied into the share.
    - ht1-lib.sh's manifest line names `qemu-system-$(uname -m)`, which is
      the same text on x86_64.
  - **make-rescuer.sh `--arch arm64`** (on aarch64 only) makes
    `rescuer-arm64.qcow2` from ubuntu26-arm64, with the UEFI branch's
    packages. Its QEMU writes a pid file, and its shutdown stops that PID
    alone. `BKO_TESTVM` picks the vm-run.
  - **ht1-queue.sh**: an arm64 line needs `rescuer-arm64.qcow2` and no
    debs, and `HT_HOLT_BIN`'s Linux bko-holt is `linux-arm64/`'s on an
    aarch64 host.
  - **ram-sizes.tsv** has estimate rows for the five on bigfoot: D 2, 3,
    4, 6 and 10.
  - **img-boot.d/linux-arm64.json** is linux.json's twin.
  - **The word.** 1ARM is the five, run from a6-run.sh. As a HoltTest2
    family it is `HoltTest2ARM`.
- **HostTest2 on Bigfoot.**
  - run-all.sh has an arm64 profile. On an arm64 host it runs the host
    steps only:
    - linux on `zfs` and `tmpfs`, sparse on `zfs` and `zfs-nocomp`. These
      are the file systems made without a loop device or a package Bigfoot
      lacks.
    - one job of a kind at a time
    - the binaries from `BIN/linux-arm64` (`LOS`)
    - no guest platform; `ANY_HOST` is refused
  - gate/loop.sh reads the commits from `bin/linux-arm64` on aarch64.
  - Every amd64 profile's `--dry-run` output (chopin, cinqcent, copland,
    cage; all platforms, and `linux,sparse`) is the same bytes as HEAD's:
    8 of 8.
- **Test2 registrations.**
  - **classes.json's `arm64` block** holds the stic guests, the holt
    family `ARM`, their `uefi` and `needs_host_zfs` (debian13-zfs-arm64).
    - plan.py's `arch_classes` merges it into stic and holt only for a run
      whose `--hosts` has an arm64 host. An `-arm64` cell is in a word's
      universe and its known names only then (`Ctx.arch_ok`), so an amd64
      run's cells, counts and plans are what they were.
    - `check_classes` counts the 77 without the block.
    - `HoltTest2ARM` without an arm64 host says so in a note.
    - An arm64 holt source needs the `holt_rescuer_arm64` cap, which only
      an aarch64 host's facts carry (facts_host.py). No amd64 host's facts
      change.
  - **holt/spec.json**: five entries, kind `linux`, `plat linux-arm64`,
    `firmware uefi`, with the rescuer replaced by `rescuer-arm64.qcow2`.
  - **stic/manifests.json**: entries with os `linux-arm64`, the
    linux-arm64 bins, and the siblings' helpers in harness and judge.
  - **stic/limits.json**: each check's own LIMIT needs and its in-guest
    part's.
  - **guest/spec.json**: entries with `plat linux-arm64`, `established
    false` and new-guest.sh. fw is `uefi`, and `uefi-sb` for
    ubuntu26-arm64.
  - **estimates.tsv**: stic, holt and guest rows, the siblings' until
    measured.
  - check-manifests.py expands per-platform files for `linux-arm64` too.
  - The `+img` cells aren't registered. They wait for an arm64 image in
    `images/`, which ImgTest2 pins (A4's is a private build), as A4 said.
  - The goldens themselves come from the run's `t2:corpus` stage. For each
    1ARM PASS:
    1. collect-dumps.sh takes the raw dumps on Bigfoot, of that run only
       (`--records RUN/t2/records`, a link to its `a6-STAMP` records).
    2. corpus_build.rs redacts them.
    3. `BKO_HOLT_GOLDEN=bless` golden_plans.rs writes their plans.
    4. collect-dumps.sh `--scan` runs leakscan on the redacted dumps and
       their plans.

    The cargo is rustup's (`~/.cargo/bin`, `RUSTUP_TOOLCHAIN=1.94.1`,
    hosts_rust_env's homes), as build-holt.sh takes it: PATH's
    `/usr/bin/cargo` is Debian's, which has no `+toolchain` (run
    20261116-161412's t2:corpus failed so).

    The result is `out/t2/corpus-arm64.patch`: the redacted dumps, their
    plan.txt and their index.tsv rows, to `git apply` after review. The
    Test2 ledger rows come from a `t2 SticTest2 ... --hosts bigfoot` run
    once that path is tried (below).

### Tests

- **`testvm/test2/tests/test_arm64_a6.py`** (28 tests, and one in test_runner_hosts):
  - **The registrations.** No `-arm64` cell in an amd64 run's stic, holt
    and guest universes. With bigfoot, exactly the five, and the amd64
    cells unchanged. The merge, known names, `check_classes`,
    `HoltTest2ARM`, the rescuer cap, every spec entry and the estimates.
  - **The five checks.** drive.py's unnamed round, and the estimates.
  - **ht1.sh.** Its PLAT, rescuer and debs. Its architecture refusal is
    run with `uname` stubbed.
  - **make-rescuer.sh's refusals**, the queue and the ram sizes.
  - **run-all.sh's bigfoot and amd64 profiles** (bash 4).
  - **a6-run.sh with a fake ssh.** It ships HEAD and not the working tree,
    starts the service in bko.slice through setpriv, refuses a second
    start under the same stamp, and removes the dry run's scratch copy.
    Usage errors never reach the host.
  - **a6-stages.sh's selection and refusals** (bash 4).
- **Tests updated for the new data.** test_runner_hosts (arm64 queue
  lines; ht1.sh's refusals with `uname` stubbed, plus an arm64 case),
  test_holt, test_guest, test_stic, test_host_caps and test_host_table.
  The p2 golden `gate-nothing-due.json` was re-blessed: only the
  `gate:selftest` cell's key moved, because drive.py, classes.json and
  estimates.tsv are its inputs. No cell, count or plan changed.
- **Results.**
  - **The Mac:** all of testvm/test2's suites, 2039+ tests, give the same
    failures and errors as HEAD c64d1bb, and none of A6's. test_arm64_a6
    passes, with 6 skipped (bash 3.2).
  - **Bigfoot:** test_arm64_a6, test_arm64_guests, test_arm64_image,
    test_arm64_builds, test_arch, test_runner_hosts, test_host_table and
    test_holt_img give HEAD's failures there, and none new.
  - **drive.py `--self-test` and check-manifests.py:** only HEAD's
    failures (openbsd8-crypt's BIN_DIR, neon24's and openbsd8-crypt's
    missing manifests, the README estimate count).

### Checks on Bigfoot

- **The dry run.** `a6-run.sh --dry-run` from the Mac shipped the private
  tree, printed the plan and the inputs, and removed its copy, in about
  4 s.
- **The smoke runs.** Each went through the real service with `--only`,
  preflight and final included. Each preflight took 9-10 s, and each
  final found the guests and the image unchanged.

  | run | stage | result | time | what it showed |
  |---|---|---|---|---|
  | a6smoke3 | stic:alpine324-arm64 | FAIL | 180 s | 25 PASS, 6 FAIL, 5 LIMIT. Every FAIL was ext4's atime: the guest had no ext4 module, so the fixture landed on the root XFS. Fixed: the check loads ext4 and loop. |
  | a6smoke4, a6smoke5 | stic:alpine324-arm64 | PASS on 2 lines; FAIL | 290 s, 367 s | The guest kernel panicked both times: "Asynchronous SError Interrupt" in restic-bko at EL0, tainted M (MACHINE_CHECK). The guest rebooted, and the check's ssh died (exit 255). The host's kernel log has nothing. The check now FAILs a run cut short ("check ran"); before, it passed on the lines it had. |
  | a6smoke6, a6smoke7 | stic:debian13-arm64 | FAIL | 332 s | 30 PASS, 1 FAIL: the guest's DVD install has no dosfstools and no network apt source, so the vfat ESP couldn't be made. Fixed: the check adds Debian's mirror in its throwaway overlay. |
  | a6smoke8 | stic:debian13-arm64 | **PASS** | 447 s | 31 PASS, 0 FAIL, 3 LIMIT |
  | a6stop1, a6stop2 | stic:fedora44-arm64, then `--stop` | ABORTED | 17 s, 28 s | a6stop1 left the guest's QEMU and its scratch: vm_boot's vm-run is setsid'd out of the stage's group. That one PID was TERMed by hand. The stop now TERMs every process the stage started that outlived its group, by the PIDs taken from /proc at the stop, then removes the run's own scratch. In a6stop2 nothing was left, and final PASSed. |

- **After the review fixes** (the rescuer VM's cleanup, DantePool's check,
  sparse-rt.sh's `/var/tmp`, hosttest2:bin's expectations, the latest
  run's choice, the value-less flags, and t2lib/batchprof.py's stic1
  estimate, which no longer counts the -arm64 guests of an unnamed round
  on x86 hosts: bkotest1all's stic1 stays 75 jobs):

  | run | stage | result | time | what it showed |
  |---|---|---|---|---|
  | a6fix1 | hosttest2:bin | PASS | 0 s | expect PASS (made for the set's commits), static, version (agree, both archivers), bko-holt 0.2.6 |
  | a6fix2 | rescuer | PASS, final FAIL | 528 s | The first rescuer-arm64.qcow2. final caught its QEMU still up: the PID check matched `/bkoscratch/...`, and vm-run passes QEMU the resolved `/home/m/bko-arm64/scratch/...`. That VM was powered off by hand and the sha256 made again. Fixed: the copy's file name is matched. |
  | a6fix3 | rescuer `--rebuild-rescuer` | PASS | 503 s | The VM powered off at the end, final PASS |
  | a6fix4, a6fix5 | rescuer, `--stop` in the install | ABORTED | 110 s, 133 s | Nothing left, but the stop waited out the install's ssh (sudo runs make-rescuer.sh in a pty session of its own, so only the script got the TERM, and bash held its trap until that ssh ended); a6fix5 needed the group's KILL. Fixed: arm64's ssh runs in the background, waited for. |
  | a6fix6 | rescuer, `--stop` in the install | ABORTED | 2 s | QEMU gone, the kept rescuer-arm64.qcow2 unchanged, final PASS |
  | a6fix7 | rescuer `--rebuild-rescuer`, hosttest2:bin | PASS | 393 s | Clean poweroff, final PASS. Bigfoot's rescuer-arm64.qcow2 (2892a2d6) is this build: a run keeps it unless `--rebuild-rescuer`. |

  The smoke run directories (a6smoke2-8, a6stop1-2, a6fix1-7) were moved
  out of `~/backotter-work/a6-run` to `~/backotter-work/a6-scratch/old-runs/`.

### Left for later

- **debian13-arm64's host name, first.** Bigfoot's image still says
  `deb13-arm64`. `sudo testvm/arm64/rename-guest-host.sh --dry-run`, then
  without, makes it `debarm13` in `/etc` and `/root`: an overlay,
  vm-mount, vm-update's COMMIT_SCRIPT, no boot. (A real vm-update would
  need the fleet lock on Chopin, which Bigfoot can't reach.) Run
  20261116-161412's dumps keep the old name, so its golden needs a new
  `holt:debian13-arm64`, not `--from-run` that run.
- **The heavy run itself.** Commit A6, then run `testvm/arm64/a6-run.sh`.
- **alpine324-arm64's guest SError panics** (above): 2 runs of 3. It may be
  KVM on the CIX P1 (the A0 risk) or Alpine's 6.18 LTS kernel. Debian's
  6.12 guest ran clean twice. Until it is understood, alpine324-arm64's
  SticTest1 and 1ARM results can't be trusted.
- **After it**, apply `out/t2/corpus-arm64.patch`. Then add
  `out/t2/limits-candidates.json`'s new needs to `stic/limits.json`, and
  `estimates-candidates.tsv`'s rows to `estimates.tsv`.
- **GateTest2 on bigfoot.** t2's `GATE_ARCH` still keeps gate cells off an
  arm64 host. The loops' cells key `linux/` binaries, so bigfoot's would
  need `linux-arm64/` ones. a6-run.sh runs the same layers by hand.
- **t2's SticTest2 and HoltTest2 runs from the Mac with `--hosts bigfoot`**
  aren't tried. The plans have the cells, but a run needs a round's
  `bin/linux-arm64` on Bigfoot and its private copy there.
- **Joining the defaults.** Bigfoot stays `default: false` until HostTest2
  and one representative per family pass (testvm/HOSTS.md, step 10). It
  then needs the user's choice on what of HostTest2's loops it can't run
  without loop devices or packages.

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
