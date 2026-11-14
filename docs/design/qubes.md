# Qubes OS support (Xen, LVM on LUKS, thin snapshots at scale)

2026-11-14. Asked for by the user: support Qubes OS, the current 4.x
release, the way Proxmox, Guix, Vanilla OS and the other 2026-11-14
distros were added. Qubes was picked for three restore paths no current
guest tests: **GRUB → `xen.gz` → the dom0 kernel through EFI**, **LVM on
LUKS with thin pools**, and **one VG holding dom0's root and every
qube's volumes, with their snapshots and revisions**. The restored disk
must boot Xen and dom0, unlock LUKS, and start the stock templates and
qubes. **The user approved this spec on 2026-11-14 with every recommended
decision (the last section).**

**Conventions.** est. marks an estimate. *Check* marks a fact the sources
don't settle, to be settled in Q0/Q1 before the code that depends on it.
Qubes's facts come from its 4.3.0 release announcement (2025-12-21: dom0
on Fedora 41, Xen 4.19, default templates Fedora 42, Debian 13, Whonix
18; 4.2 supported to 2026-06-21, so **4.3 is the only current 4.x**), its
automated-tests documentation and forum threads on running Qubes under
KVM, seen through a web search on 2026-11-14, and what is known of the
4.1/4.2 installer's disk layout. Nothing has been downloaded or
installed. Code references are to local `main` at d5e8686 plus the
uncommitted tree (Vanilla OS's thin-pool work is in it, uncommitted).

## Summary

- Qubes OS 4.3 becomes a supported system for bko-holt, with one UEFI
  QEMU guest, **`qubes43`**, installed unattended by a kickstart, booted
  under nested virtualisation on the farm, in **1Obscure** (Decision 7).
- **Nested virtualisation is available on all four hosts** (checked
  2026-11-14: AMD-V, `kvm_amd nested=1`, host AMD-Vi, QEMU 10.0.13). The
  open question is only whether Xen 4.19 runs well under it with QEMU's
  emulated IOMMU; **Q0 is a one-day feasibility probe that gates
  everything else** (Decision 1).
- **dom0 has no network**, so bko-holt dump runs only from the rescue
  image (`dump --disk`, as Sculpt and Redox); the post-restore check runs
  in dom0 as a one-shot systemd unit that reports on the Xen serial
  console. No SSH into dom0 (Decision 4).
- **Load's thin support (Vanilla V3/V7) makes pools and plain thin LVs
  only.** Qubes needs four more things, the core of this work (Q2-Q4):
  thin **snapshots** made as snapshots (in origin order, between restore
  steps); **raw block volumes** (a qube's volumes are disk images, not
  filesystems bko-holt can mount); **activation-skip** snapshot LVs read
  and made; and a pool **fit check that counts shared blocks**, not the
  sum of the thin LVs.
- Recommended design: **dom0's root file by file** (as any Linux), **every
  `vm-*` volume as a sparse raw image** with snapshot chains restored as
  base image + changed blocks (Decision 2).
- Guest size: 64 GiB virtual disk, ~22 GiB used est., 10 GiB RAM, 4 vCPU;
  on /bkoscratch (`DISK_OVER=1`), Chopin and Copland (Decision 6).
- Steps Q0-Q9, ~7 d est. after Q0 passes.

## Why

No guest boots a hypervisor: every restored boot today hands GRUB's
`linux` line straight to a kernel. Qubes's GRUB menu loads `xen.gz` with
`multiboot2` and the dom0 kernel and initramfs as `module2`s, and its
`/etc/default/grub` splits options between `GRUB_CMDLINE_XEN_DEFAULT` and
`GRUB_CMDLINE_LINUX`: a load that rewrites `rd.luks.uuid=` in the one but
not the other, or regenerates the menu without `20_linux_xen`, boots
nothing.

LVM on LUKS is tested (ubuntu26-luks, elementary8-luks), and thin pools
are (vanilla3: one pool, two thin LVs). Nothing tests **thin snapshots**:
load refuses them today (`thin_of`, `holt/src/load/commands.rs:846`,
"version 1 doesn't make thin snapshots"). A stock Qubes install has two
pools and ~40-60 thin LVs est., about a third of them snapshots
(revisions), over-provisioned several times.

## What Qubes OS 4.3 is (as far as bko cares)

| Thing | Qubes 4.3 (*check* all in Q1) | Like |
|---|---|---|
| Disk | GPT: p1 ESP 600 MiB FAT at /boot/efi; p2 `/boot` ext4 1 GiB; p3 LUKS2 (argon2id) holding PV of VG **`qubes_dom0`** | Fedora |
| VG `qubes_dom0` | `swap` linear; thin pool **`root-pool`** with thin LV `root` (dom0's ext4 /, ~20 GiB virtual); thin pool **`vm-pool`** with every qube volume | vanilla3 (pools), new (snapshots) |
| Qube volumes | `vm-NAME-root` (templates only: a whole **disk image**, GPT with BIOS-boot, ESP and ext4 root inside), `vm-NAME-private` (ext4 on the whole LV, no partition table), `vm-NAME-volatile` (remade at each start); revisions `vm-NAME-private-TIMESTAMP-back`, thin snapshots kept per `revisions_to_keep` (default 1 est.); `-snap` LVs exist only while a qube runs | new |
| Snapshot links | at a qube's stop Qubes renames `private` to `private-TS-back` and `private-snap` to `private`, so the live volume's `origin` is the newest revision: chains of snapshots, by name, in the VG metadata; LVM gives snapshots **activation skip** (`k`) | new |
| Boot | GRUB2 EFI (`\EFI\qubes\grubx64.efi`, NVRAM entry "Qubes"), no shim, Secure Boot off; menu by `grub2-mkconfig`, `20_linux_xen`: `multiboot2 /xen-4.19.*.gz`, `module2 /vmlinuz-*`, `module2 --nounzip /initramfs-*.img` | Fedora GRUB, new Xen entry |
| Cmdlines | Xen: `GRUB_CMDLINE_XEN_DEFAULT` (`console=none dom0_mem=... ucode=scan smt=off ...`); dom0: `rd.luks.uuid=luks-UUID rd.lvm.lv=qubes_dom0/root rd.lvm.lv=qubes_dom0/swap ...` | |
| Initramfs | dracut (Fedora 41), with the LUKS UUID and LV names in it | Fedora |
| os-release | `ID=qubes`, `VERSION_ID=4.3` est., `ID_LIKE` fedora est. | osrel.rs row |
| Qubes's own state | `/var/lib/qubes/qubes.xml` names pools and volumes by VG, pool and LV **name**; vm-kernels under `/var/lib/qubes/vm-kernels` | names kept, UUIDs may change |
| Network | none in dom0; NICs go to `sys-net` by PCI passthrough (needs IOMMU), USB controllers to `sys-usb` | |

## Gate 1: running Qubes under the farm's KVM

**Checked 2026-11-14 on all four hosts:** AMD Ryzen 5 5600H (chopin,
cinqcent) and Ryzen 7 255 (cage, copland), `Virtualization: AMD-V`,
`/sys/module/kvm_amd/parameters/nested` = 1, the host's own IOMMU
`ivhd0`, QEMU 10.0.13. So L0 KVM can run Xen as L1 with SVM, and Xen's
guests as L2.

What's known from Qubes's side: its openQA installs Qubes in KVM; older
docs say HVM nested was crash-prone and **PV qubes were the workaround**;
later forum reports run HVM/PVH qubes nested under KVM with tweaks. All
reports are on Intel hosts with `intel-iommu`; nothing seen for Xen on
nested AMD with QEMU's `amd-iommu`. So **Q0** tries, in order, on Chopin
and Copland (Cinqcent is out until about 10:30 today):

1. `-machine q35,kernel-irqchip=split -cpu host -device
   amd-iommu,intremap=on` (Xen should see an IVRS table and enable
   AMD-Vi);
2. the same with `-device intel-iommu,intremap=on,caching-mode=on` (an
   emulated VT-d on an AMD CPU: *check* Xen accepts a DMAR table there);
3. no IOMMU: the installer warns, and PCI passthrough is off.

For each: does the installer ISO boot Xen and Anaconda; does a minimal
install finish; do PVH qubes start; does `sys-net` with **no** PCI device
start (HVM with a stubdomain, the heaviest L2 case). Record times.

**What the test needs is less than a usable Qubes:** Xen and dom0 booting,
LUKS, the pools, and the templates and app qubes starting. It doesn't
need networking or USB. So the guest never assigns PCI devices: no
`sys-usb`, `sys-net` with nothing assigned (or none, Decision 3), and
config 3 is enough if 1 and 2 fail.

**Fallbacks**, in order (Decision 1):

- **A. Qubes with PV qubes.** If PVH/HVM fail nested, kickstart `%post`
  (or the first-boot unit) sets `virt_mode pv` on the templates and the
  qubes the check starts. PV needs no SVM at all, which is what openQA
  did. The disk layout is unchanged, so the test of bko is unchanged.
- **B. "Xen-shaped" guest, no Qubes.** If Xen won't boot or dom0 won't
  reach a login: Fedora 41 with the `xen` package, on Qubes's exact disk
  layout (the table above, same names), with `vm-pool` filled by a script
  that copies Qubes's volume lifecycle (templates' disk images,
  ext4 privates, `lvcreate -s` revisions with renames). The check boots
  Xen + PV dom0 (no SVM needed) and compares each LV's checksums and
  each snapshot's origin. It loses "start the stock qubes" and the real
  qubes.xml.
- **C. No Xen.** B without the hypervisor: tests only the LVM part. Not
  worth a guest of its own; the thin-snapshot scenarios in `scenarios.rs`
  and a SticTest2-style LVM case would cover it.

## Gate 2: thin LVM in load

**What exists (Vanilla OS V3/V7, uncommitted):** `thin_of`
(`holt/src/load/commands.rs:762`) reads each VG's pools and thin LVs;
`vg_metadata` writes the VG without them for `vgcfgrestore`; the
assemble stage runs `lvcreate --type thin-pool` (sizes, chunk size,
discards, zeroing, `--poolmetadataspare`) and `lvcreate --type thin` per
thin LV in `device_id` order; tags, activation skip and read-only are
carried; `lvchange --errorwhenfull y` where the source had it;
`plan.rs:64` checks the rescuer has thin-pool segtypes, `thin_check` and
`dm-thin-pool`. More than one pool per VG is in the data model
(`Thin.pools`) but no guest has tested it.

**Refused in v1, all needed by Qubes:** a thin LV with `origin`
(snapshot), `--fit-largest` with thin pools, and (implicitly) any thin
LV whose content isn't a filesystem the format stage can make.

**What load must add (Q2-Q4):**

1. **Thin snapshots, made as snapshots.** For each pool, a forest from
   the `origin` links. A root (no origin) is created as today. A snapshot
   is created with `lvcreate -s VG/ORIGIN -n NAME` **after its origin's
   content is restored**, then its own differences written. That moves
   these lvcreates out of the assemble stage into the restore stage, the
   way btrfs's shared snapshots already wait for the restore
   (`commands.rs:2295`). Activation skip: snapshots are made with the
   source's `-k` flag and activated with `-K` to be written.
   `external_origin` stays refused.
2. **Raw volumes.** A content kind beside "filesystem": the LV's bytes.
   Dump stores each `vm-*` LV as a sparse image (zero runs not stored;
   unprovisioned thin blocks read as zeros), restic/rustic dedup the
   revisions' shared chunks in the repository. Load writes the base
   image skipping zero runs (a fresh thin LV reads zeros, so they stay
   unprovisioned), then for a snapshot writes **only the blocks that
   differ from its origin's image** (compare chunk by chunk; `thin_delta`
   on a metadata snapshot is the faster alternative, *check* in Q2), so
   the pool's sharing survives. Which LVs are raw: a dump-time rule,
   `vm-*` in a pool that qubes.xml names (Decision 2), and generally any
   LV whose content probes as a partition table or nothing bko-holt can
   mount (today such an LV is made empty, as Proxmox's zvols are).
3. **Reading snapshots in dump.** The revisions are inactive
   (activation skip). Dump activates each with `-K`, read-only, reads
   it, and deactivates it. Two snapshot ext4s have the same filesystem
   UUID; raw capture never mounts them, and the probe must key them by
   device, not UUID (*check* where `probe` dedups by UUID).
4. **Pool fit by provisioned data.** The used-data check compares the
   source pool's `data_percent` × size (and metadata's) with the target
   pool, not the sum of the thin LVs (several times the pool). Plan
   prints both. `--fit-largest` may grow `vm-pool` to the target disk;
   shrinking a pool below its used data is refused.
5. **Scale.** ~60 lvcreates and as many writes: one `--devices` filter
   built once, lvcreate batched where LVM allows (*check*), the plan
   grouping volumes by qube instead of 60 bare lines.
6. **Volatile and leftovers.** `vm-*-volatile` is made empty (Qubes
   remakes it at start); a leftover `-snap` (unclean shutdown) is
   restored like any snapshot and noted.

## The design

### Detection

`osrel.rs`: a SUPPORTED row `("qubes", "4.3", "Qubes OS 4.3")`, own row,
appended per the shared-lines notes. `os.product` "Qubes OS 4.3".
`boot.xen`: present when `/boot/xen*.gz` and a `20_linux_xen` menu
exist: the Xen version, `GRUB_CMDLINE_XEN_DEFAULT`, the `multiboot2`
entries found in grub.cfg. `qubes`: the pools and volume names from
`qubes.xml` (no other content), so plan can say which qube each LV is.

### `dump`

Only `dump --disk DEV` from the rescuer, with LUKS opened by passphrase
(console-boot types it, as ubuntu26-luks). dom0's root, `/boot` and the
ESP file by file, as Fedora. Every `vm-*` LV raw (above). `swap`
recorded, not backed up.

### `load`

- **Assemble:** LUKS with its UUID kept (as today), the VG by
  `vgcfgrestore`, both pools, the root thin LVs; snapshots wait.
- **Restore:** dom0 files; the raw volumes base-first, each snapshot
  created then filled with its changed blocks.
- **Boot:** the Fedora GRUB arm, plus: `GRUB_CMDLINE_LINUX`'s
  `rd.luks.uuid=` rewritten only if the LUKS UUID changed (it doesn't by
  default), `GRUB_CMDLINE_XEN_DEFAULT` left alone, `grub2-mkconfig` in the
  chroot with `20_linux_xen` (*check* it finds `xen*.gz` offline), dracut
  regenerated, the "Qubes" NVRAM entry made (`--no-nvram` as Proxmox's).
  No shim, no Secure Boot.
- **Refused:** Qubes's btrfs layout (an installer option, Out of scope),
  Qubes 4.2 or older, and a source whose qubes.xml names a pool not in
  the dump.

### `redact.rs`

GENERIC_LABELS / VG and pool names: `qubes_dom0`, `root-pool`,
`vm-pool`; GENERIC_HOSTS `dom0`; SYSTEMS `qubes`. Qube names in LV
names: the stock ones (`sys-net`, `sys-firewall`, `default-dvm`,
`personal`, `work`, `untrusted`, `vault`, `fedora-42-xfce`,
`debian-13-xfce`, ...) are generic words; a qube the user named would
be redacted like a hostname.

## Guest

**`qubes43`**, UEFI (OVMF without Secure Boot), q35 with the IOMMU Q0
picks, 4 vCPU `-cpu host`, **10 GiB RAM** (dom0 4 GiB max + the
templates and qubes the check starts, one at a time), **64 GiB** qcow2,
~22 GiB used est. (dom0 ~6, Fedora 42 template ~7, Debian 13 ~4, privates
and revisions ~3, swap). No Whonix (Decision 3). 1Obscure.

**Unattended install:** Qubes's Anaconda takes `inst.ks=` (*check* the
4.3 ISO; its own openQA and the 2022 "testing on physical laptops" post
pass a kickstart). The ISO goes in `rebuild-inputs` with its published
SHA-256 and signature checked. Kickstart: autopart LVM thin on LUKS
(the installer's default layout, so the guest is what users get), the
LUKS passphrase from the test key file (not in the repo), `%addon` for
Qubes's initial setup with: default templates Fedora and Debian, default
system qubes **without** `sys-usb` and with nothing assigned to
`sys-net`, default app qubes, no Whonix (*check* the addon's option
names; if initial-setup can't be driven by kickstart, a `%post` that runs
`qubes-initial-setup`'s own steps at first boot). `%post` adds the
**check unit** (below) and the serial console: Xen `console=com1
com1=115200,8n1`, dom0 `console=hvc0`.

**Revisions on purpose:** after install, a first-boot script starts and
stops `work` and `personal` twice each and the Debian template once,
so there are `-back` revisions and snapshot chains of length 2, and
sets `revisions_to_keep 2` on `work` (one chain of three).

**The check unit** (`bko-qubes-check.service`, dom0, oneshot, after
`qubesd`): `qvm-start` the Fedora and Debian templates, `sys-firewall`,
`work`, `personal`, one after another; `qvm-run --pass-io` a command in
each that prints its hostname and checksum of a seeded file; `qvm-ls
--raw-data`; LVM: each LV's origin and `k` flag; then shuts them down
and writes one `BKO-QUBES-CHECK ...` result line per item to `/dev/hvc0`
and a log in `/var/log`. ht1 reads the Xen serial file and judges. This
is test-only code in the guest, as sculpt-check.sh is test-only.

**Updates:** `qubes-dom0-update` needs `sys-net`'s network: the guest
can't take updates the normal way, so `vm-update` refuses it; it is
refreshed by a reinstall from a newer ISO (like Sculpt has its own path;
**Decision 5**). This conflicts with "images take OS updates" only for the
guest, not the rescue images.

**Host placement:** on /bkoscratch (`DISK_OVER=1`, like vanilla3), not
RAM. Chopin and Copland (Decision 6): Cinqcent's disk is at 79 % today
(not 69 %), and the guest needs no host ZFS. 10 GiB guest RAM fits a
small host's 27 GiB.

## Tests

- **Unit/scenarios** (`holt/src/scenarios.rs`): `qubes_guest` (two pools,
  50 thin LVs, chains of 1-3 snapshots, `k` flags, raw volumes), refusals
  (`external_origin`, a pool too small for its provisioned data, a
  chain whose origin isn't in the dump).
- **Corpus:** `holt/tests/corpus/qubes43` (src.json, golden plan.txt),
  redacted and leakscanned as vanilla3's.
- **HoltTest1 1Obscure:** ht1-offline.sh's path (dump --disk in the
  rescuer, both dumps), the restore, a boot judged by the check unit's
  serial lines; judge.py rules: snapshot LVs' UUIDs and device ids new,
  origins by name equal, each raw volume's checksum equal, pool used data
  within 5 % (sharing kept). Both Q0 hosts.
- **SticTest1:** as Sculpt: the offline path only (bko can't run in dom0
  without a network); one entry, dom0's root backed up from the rescuer's
  mount, restored and compared. *Check* the archivers' offline path takes
  a LUKS-opened LV.
- **Test2:** registered in the batched Test2 pass: classes.json Obscure +
  uefi + `nested_virt` (a new class flag: the guest runs only on hosts with
  `kvm_amd`/`kvm_intel nested=1`, all four today), holt/spec.json,
  guest/spec.json (GuestTest2's check = the check unit's lines),
  estimates.tsv, plan.py counts, world.py.
- **BackShot1:** not proposed. The Thinkpad would need Qubes installed
  (it may run it fine, VT-d on a Thinkpad is usual), and the user didn't
  ask.

## Shared lines (with TrueNAS, Aeon/MicroOS, ZFSBootMenu, Ubuntu Core and the in-flight distros)

osrel.rs SUPPORTED (own row) and the supported_names string; redact.rs
GENERIC_* and SYSTEMS; boot.rs PACKAGES (+`xen`, `xen-hypervisor`,
`grub2-xen-pvh` est.) and fingerprint.py's mirror; the thin code in
`load/commands.rs` (**Vanilla's**: a note in the shared folder before Q2
starts, and Q2 waits until Vanilla's thin hunks are committed or Vanilla
says it is done with them); the Aeon/MicroOS session may touch
snapper/btrfs, not LVM; Ubuntu Core may add raw-ish partitions (snaps'
seed) — if it also needs "raw volume" capture, **one session writes it**
(note in the shared folder, Q3). holttest1d README 1Obscure row, one edit
at a time; check-manifests.py count; classes.json in the batched pass.

## What Q0 found (2026-11-14: PASS, full Qubes with PVH qubes, no fallback)

- **ISO:** `Qubes-R4.3.2-x86_64.iso`, 8,459,569,152 bytes, from
  `mirrors.edge.kernel.org/qubes/iso/`, SHA-256 `13bc750b…26776`, in
  `/bkoscratch/rebuild-inputs/qubes43/` on Chopin and Copland (with its
  `.asc`, `.DIGESTS`, `.DIGESTS.asc` and both keys). The DIGESTS signature
  checks with the Qubes OS Release 4.3 Signing Key (F3FA 3F99 … 7F3F
  ADA4), which the Qubes Master Signing Key (427F 11FD 0FAA 4B08 0123 F01C
  DDFA 1A3E 3687 9494) signs.
- **Templates on the ISO:** `fedora-44-xfce` (not 42: 4.3.2 ships
  Fedora 44), `debian-13-xfce`, `whonix-gateway-18`,
  `whonix-workstation-18`. The guest's Fedora template is fedora-44-xfce.
- **Boot media:** El Torito BIOS (`images/eltorito.img`) and UEFI (a
  9,844-sector `efiboot.img`); EFI GRUB reads `/EFI/BOOT/grub.cfg` from
  the ISO9660 tree (label `QUBES-R4-3-2-X86-64`). Its entries are
  `multiboot2 /images/pxeboot/xen.gz console=none` + `module2
  /images/pxeboot/vmlinuz inst.repo=hd:LABEL=… quiet` + `module2
  initrd.img`, plus a kernel-latest entry (7.2.6-1.qubes.fc41).
- **Unattended install needs no remaster:** grub.cfg has an **OEM entry**
  that becomes the default when a volume labelled `QUBES_OEM` exists, with
  `inst.ks=hd:LABEL=QUBES_OEM`. The probe gives it a 16 MiB FAT image
  (`mformat -v QUBES_OEM`, `ks.cfg` at its root). The installer's own
  console stays `console=none` (screenshots via QEMU's monitor); the
  installed system gets serial consoles from `%post`.
- **Kickstart addon** (qubes-anaconda-addon `service/kickstart.py`):
  `%addon org_qubes_os_initial_setup`, one `param value` per line;
  booleans `system_vms`, `disp_firewallvm_and_usbvm`, `disp_netvm`,
  `disp_preload`, `default_vms`, `whonix_vms`, `whonix_default`, `usbvm`,
  `usbvm_with_netvm`, `allow_usb_mouse`, `allow_usb_keyboard`,
  `create_default_tpool`, `skip`; values `default_template`,
  `templates_to_install` (space-separated), `lvm_pool VG/POOL`. Its tasks
  (templates, default pool, dom0 config, default qubes, DVM, network)
  run as Anaconda install tasks, not at first boot.
- **Nested Xen works; the stock install doesn't boot as is.** With QEMU's
  `amd-iommu` on Cage, Xen 4.19.5 enables AMD-Vi ("IOMMU 0 Enabled"),
  SVM with HAP (4k/2M/1G) and HVM, and starts dom0, which then halts:
  `Xen hypervisor allocated kernel memory conflicts with E820 map`. The
  Linux PV dom0's kernel image is pseudo-physical 2-64 MiB, and Debian's
  `OVMF_CODE_4M.fd` marks 0x800000-0x807fff, 0x80b000-0x80bfff and
  0x811000-0x8fffff ACPI NVS (OVMF's SEC/PEI work areas, kept for S3
  resume). Same stall with `intel-iommu` on Copland (no serial then,
  so not confirmed it's the same line). Fix tried next: `-global
  ICH9-LPC.disable_s3=1`, which makes OVMF allocate those areas as boot
  services data instead; the guest keeps it (a Qubes guest never sleeps).
  **It works:** the EFI RAM map then has nothing below 0x7ed3f000 but
  usable RAM, and dom0 (Linux 6.18.51-1.qubes.fc41) boots on. It then
  oopses at 2 s in `print_s5_reset_status_mmio` (an AMD Zen initcall that
  ioremaps the FCH's ACPI MMIO at 0xfed80000, which QEMU's q35 doesn't
  have; under Xen PV the read faults): `Kernel panic - not syncing:
  Fatal exception`. Next: `initcall_blacklist=print_s5_reset_status_mmio`
  on dom0's command line (the debug ISO's, and `GRUB_CMDLINE_LINUX` in
  the kickstart's `%post`). The guest keeps it too, and a restore must
  carry it (it is in `/etc/default/grub`, a restored file, and in the
  regenerated grub.cfg). Both farm CPU types are AMD Zen, so every host
  needs it.
- **With both, the kickstart install completes** on Cage (amd-iommu,
  8 GiB): 7 min, 11 GiB written, "Configuring addons", `%post`, power-off.
  The installed disk boots GRUB on serial (`GRUB_TERMINAL="serial
  console"`), loads Xen 4.19.5 and Linux 6.18.51, and dom0 reaches
  systemd-cryptsetup's "Please enter passphrase for disk luks-UUID". The
  addon's template install doesn't appear in the install log:
  `initial-setup` is installed, so the templates and default qubes are
  presumably made at first boot (to confirm). Qubes's
  `GRUB_CMDLINE_XEN_DEFAULT` carries `console=none` and Xen takes the
  last `console=`, so `%post` replaces it rather than prepending.
- **First boot stops at `initial-setup`'s GUI**, by design: after LUKS
  (typed over serial; systemd-ask-password redraws its prompt with a `*`
  per key, so a driver must not take a redraw for a new prompt), qubesd
  and qmemman start, then initial-setup shows a "QUBES OS" spoke marked
  incomplete. In qubes-anaconda-addon (main and release4.3)
  `gui/spokes/qubes_os.py` has `completed` = `self.seen`, set only in
  `apply()` when a human leaves the spoke; kickstart values only pre-fill
  it (here they didn't even do that: the spoke showed Qubes's defaults,
  Whonix and sys-usb ticked; cause not settled). initial-setup reads
  `/root/anaconda-ks.cfg`. Qubes's own openQA (`tests/firstboot.pm`)
  clicks through it. Q0 drove it by QEMU `sendkey` from the monitor:
  Enter on the spoke, Tab order Done → Debian 13 → Fedora 44 → Whonix 18
  → Default → system qubes → disposable sys-firewall/sys-usb → disposable
  sys-net → app qubes → sys-usb (…), Space toggles, Alt+D Done, Alt+F
  Finish configuration; it then shows "Installing TemplateVM
  debian-13-xfce". **Q1 choice:** drive the same keys from make-qubes.sh
  (Qubes's own code path, as openQA), or `firstboot --disable` plus a
  first-boot unit of ours running the addon's tasks (`qvm-template
  install`, `qubesctl --all state.highstate`, …). Recommended: the keys;
  the unit would be our copy of Qubes's setup, drifting with each release.
  The stock menu passes `console=none` to Xen, so the probe uses a
  debug copy of the ISO (`q0-dbg.iso`: the same tree, both `grub.cfg`
  copies, the ISO9660 one and `efiboot.img`'s, replaced by one entry
  with Xen on com1 and dom0 on hvc0, `inst.text`, `inst.ks=hd:LABEL=QUBES_OEM`;
  made with `xorriso -boot_image any replay -map`).
- **Q0 PASS (Cage, amd-iommu, 8 GiB, 2026-11-14 14:15).** After the
  spoke, initial-setup installed both templates and made the default
  qubes (about 10 min). The probe then logged in on hvc0 and found:
  - os-release `ID=qubes`, `VERSION_ID=4.3`, `PRETTY_NAME="Qubes OS 4.3.2
    (R4.3)"`; Xen 4.19.5, `virt_caps pv hvm hvm_directio pv_directio hap`;
    dom0 4080 MiB (`dom0_mem=min:1024M dom0_mem=max:4096M`), 2955 MiB
    free of 8187 with three qubes running.
  - Disk: vda1 600 MiB vfat /boot/efi, vda2 1 GiB ext4 /boot, vda3 LUKS2
    `luks-UUID` → VG `qubes_dom0`: `root-pool` thin (20 GiB, tmeta 40 MiB)
    with thin `root` (20 GiB, ext4, 27.6 % used); `swap` linear 3.93 GiB;
    `vm-pool` thin (34.48 GiB, tmeta 36 MiB, 34 % used); `lvol0_pmspare`
    40 MiB. NVRAM `Boot0004* Qubes OS HD(1,GPT,…)/\EFI\qubes\grubx64.efi`.
  - grub.cfg: `multiboot2 /xen-4.19.5.gz placeholder console=com1 …
    dom0_mem=… ucode=scan smt=off gnttab_max_frames=2048 …`, `module2
    /vmlinuz-6.18.51-1.qubes.fc41.x86_64 placeholder root=/dev/mapper/qubes_dom0-root
    ro … rd.luks.uuid=luks-UUID rd.lvm.lv=qubes_dom0/root
    rd.lvm.lv=qubes_dom0/swap …`, `module2 --nounzip /initramfs-….img`.
    Anaconda also copied the installer's own extra arguments into
    `GRUB_CMDLINE_LINUX` (the blacklist appears twice).
  - Qubes: pools `varlibqubes` (file), `linux-kernel`, `vm-pool`
    (lvm_thin). Qubes dom0, debian-13-xfce and fedora-44-xfce templates
    (pvh), default-dvm, default-mgmt-dvm, personal, work, untrusted,
    vault (pvh AppVMs), sys-firewall (pvh DispVM of default-dvm), sys-net
    (**hvm**, with its stub domain `sys-net-dm`). No sys-usb, no Whonix.
  - `qvm-start work` 10 s, the Debian template 16 s, `qvm-run --pass-io`
    works (uname, os-release), `qvm-shutdown --wait --all` 8 s. No
    domain crashed. The only journal errors are parport DMA notes and
    initial-setup's hub warning.
  - **Thin LVs after one shutdown:** 26 in `vm-pool`, with chains: e.g.
    `vm-debian-13-xfce-private` ← origin `…-private-1794662077-back` ←
    origin `…-private-1794661636-back` (three deep), the same for its
    `root`; `vm-work-private` ← `…-1794662082-back`. Template root
    revisions differ in **virtual size** from their successor (a
    10 GiB `…-root-1794661610-back` beside 20 GiB roots: the template
    RPM's image was grown). While qubes run there are `-snap` LVs whose
    origin is the template's root (`vm-sys-net-root-snap` ← 
    `vm-fedora-44-xfce-root`) or the DVM's private, and `-volatile` LVs.
  - **No activation skip anywhere** (attrs `Vwi-a-tz--`): Qubes creates
    its snapshots with `-kn`. Gate 2's point 1 needs no `-K` for Qubes,
    but load must still make snapshots with the source's flag (lvcreate
    -s defaults to `-ky`).
  - Size: 22 GiB of qcow2 after setup and one round of qube starts. Kept
    at `/bkoscratch/qubes-q0/amd/disk.qcow2` on Cage for Q1.
  - Not run: intel-iommu and no-IOMMU (not needed: amd-iommu works),
    and the 10 GiB size (8 GiB works; keep 10 GiB for the check unit's
    five qubes).
- **Hosts:** Chopin ran it behind other sessions' reservations; Copland's
  free memory (14 GiB with 7 GiB of RAM-disk room counted) doesn't fit a
  10 GiB guest while other work runs there.

## What Q1 found (2026-11-14, Cage)

- **make-qubes.sh builds qubes43 unattended** (`testvm/make-qubes.sh`,
  `testvm/qubes/`: `qubes-drive.py`, `ks.cfg.in`, `install-grub.cfg`,
  `revisions.sh`, `bko-qubes-check.sh` and `.service`). Run 2 on Cage
  (`cmp-run.sh --reserve 0 10`, work dir `/bkoscratch/qubes-q1/qubes43`):
  install 435 s; first boot 938 s, of which initial-setup's templates
  and qubes ~11 min; the check boot ~3 min. The qcow2 is 18 GiB on disk
  (24 GiB apparent). The prepared ISO is
  `Qubes-R4.3.2-x86_64-bko-3472d477b6a7.iso` in the ISO dir.
- **initial-setup by keys works first time**: qubes-drive.py finds the
  focus from GTK's dotted focus ring rather than counting Tabs, and checks
  every box before Done (`setup-08-spoke-before-done.png`: Debian 13 +
  Fedora 44, Whonix 18 off, system and app qubes on, sys-usb off,
  vm-pool created).
- **Two driver bugs, fixed:** (1) `timeout` puts its command in a process
  group of its own, so `qvm-run --pass-io` run from the serial login shell
  read the terminal, got SIGTTIN and hung until the timeout ("seed in work
  after 300 s"); `revisions.sh` now starts with `exec </dev/null`, and
  bko-qubes-check.sh's qvm-run reads /dev/null too. (2) After `DONE PASS`
  the check boot's login failed: audit lines landed on the
  `dom0 login: ` line and the one Enter came before the getty; `login()`
  now presses Enter every 15 s until a prompt ends a line.
- **The check boot (run 2): every item PASS** — qubesd at once; starts
  fedora-44-xfce 9 s, debian-13-xfce 8 s, sys-firewall (already up with
  sys-net), work 10 s, personal 10 s; `qvm-run` in each gives its hostname
  and the round-2 seed's hash in work and personal; 11 qubes; all down
  in 9 s; LVM: 26 thin LVs, 14 with an origin, work's private chain 3.
- **Revision chains** (after revisions.sh; `revisions.txt`):
  `vm-work-private` ← `…-1794670270-back` ← `…-1794670226-back`, the same
  shape for personal, debian-13-xfce private and root; fedora-44-xfce root
  ← `…-1794670062-back` plus a lone 10 GiB `…-root-1794670030-back` (the
  RPM's image before it was grown); sys-net private ← one revision.
  **Qubes 4.3's default `revisions_to_keep` is 2, not 1**: personal (left
  at the default) keeps two as work does; so most volumes that have been
  used reach chains of 3. Every boot of the guest runs the check unit,
  which stops qubes and so adds revisions (bounded by the setting): the
  chains a dump sees depend on how many boots the guest has had.
- **The ram-sizes.tsv row moves to Q7**: its numbers are ht1.sh's measured
  peak, which only a HoltTest1 run gives.
- **vm-run can't boot qubes43 yet**: it has no word for `amd-iommu`,
  `kernel-irqchip=split` or `ICH9-LPC.disable_s3`. make-qubes.sh writes
  them to `WORK_DIR/qemu-options`; vm-run needs an option for them before
  Q7 (a shared file).

## Pieces to build (ordered, est.)

- **Q0 Feasibility probe** (1 d): the three IOMMU configs on Chopin and
  Copland, a minimal manual install, PVH/HVM/PV qube starts; a note with
  times. **Stop and report**: the choice of full Qubes vs fallback A/B
  is the user's if the recommended one fails.
- **Q1 Guest** (1.5 d): `make-qubes.sh` + kickstart, ISO in
  rebuild-inputs, revisions script, check unit, `rebuild-one.sh` recipe,
  ram-sizes.tsv row; settle every *check* in the table above into a
  "What Q1 found" section.
- **Q2 Thin snapshots in load** (1.5 d, High): origin forests, snapshot
  lvcreates in the restore stage, `-k`/`-K`, multiple pools, fit by
  provisioned data, scenarios.
- **Q3 Raw volumes** (1.5 d, High): dump's sparse capture of chosen LVs
  (activating revisions `-K` read-only), load's base + changed-block
  writes, scenarios; generic (any LV, not only Qubes's).
- **Q4 Xen boot arm** (0.5 d, High): `boot.xen`, the Fedora GRUB arm's
  Xen cmdline handling, NVRAM "Qubes", osrel row, redact words.
- **Q5 Corpus + golden plan** (0.3 d).
- **Q6 SticTest1 offline entry** (0.3 d).
- **Q7 HoltTest1 1Obscure** (1 d incl. runs): ht1 hand-off to the offline
  path, judge rules, serial check reading; PASS on both Q0 hosts.
- **Q8 Docs** (0.2 d): holt/README.md, layload.md (snapshots, raw
  volumes, Xen), the 1Obscure row, this file's "found" sections.
- **Q9 Test2** registrations: in the batched pass.

## Risks

- **Xen nested on AMD with QEMU's emulated IOMMU** is untested by anyone
  we know of. Q0 exists for this; fallback A keeps the whole test of bko.
- **Slowness:** L2 qubes under nested SVM are slow (est. 1-3 min per
  qube start); the check starts five, so ~15 min per boot check.
- **Kickstart for initial-setup** may not be drivable; the `%post`
  fallback is more code.
- **Changed-block writes** must compare against what the origin *now*
  holds on the target, not the source: they are the same bytes only if
  the base write was exact. The judge's per-volume checksum guards it.
- **The raw-volume rule could catch LVs a user meant as filesystems.**
  Decision 2 keeps it to qubes.xml's volumes plus LVs nothing can mount.
- **Thin metadata at scale:** a too-small `--poolmetadatasize` fails
  late; load uses the source's metadata size, never smaller.

## Out of scope

Qubes's btrfs (reflink pool) layout and file pools; Qubes 4.2; Whonix
qubes; HVM qubes with PCI passthrough; Windows qubes; restoring onto a
disk too small for `vm-pool`'s provisioned data; `qvm-backup` interop
(Qubes's own backup format); online dump in dom0.

## Decisions for the user

1. **Gate and fallback.** Recommended: Q0 first; full Qubes 4.3 with PVH
   qubes if it works, else fallback A (PV qubes), else stop and ask
   before building fallback B (Xen-shaped Fedora).
2. **How qube volumes are backed up.** Recommended: **raw sparse images**
   for `vm-*` volumes with snapshot chains restored as base + changed
   blocks (fs-agnostic: template roots are whole disk images, Windows
   qubes would work too). Alternative: file by file per volume, which
   needs partition tables inside LVs and loses sharing unless each
   revision is rsync-ed onto its snapshot; more code, no gain here.
3. **Which qubes the guest has.** Recommended: Fedora 42 and Debian 13
   templates, `sys-net` (no PCI device), `sys-firewall`, `default-dvm`,
   `personal`, `work`, `untrusted`, `vault`; no `sys-usb`, no Whonix.
4. **dom0 access.** Recommended: no network in dom0; offline dump only;
   a test-only check unit that reports on the serial console.
   Alternative: a test-only NIC kept in dom0 with sshd, closer to other
   guests' harness but unlike any real Qubes.
5. **Guest updates.** Recommended: refreshed by reinstalling from a
   newer ISO (no `vm-update` arm), checked monthly against Qubes's
   release list.
6. **Hosts.** Recommended: Chopin and Copland, on /bkoscratch, not
   Cinqcent (disk at 79 %).
7. **HoltTest1 word.** Recommended: **1Obscure**.
