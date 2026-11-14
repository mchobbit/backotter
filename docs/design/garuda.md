# Garuda Linux support

2026-11-14. Asked for as the next Arch-based desktop distribution after
EndeavourOS and Manjaro (commit 6715f43). **Approved 2026-11-14** by the
user, with every recommended decision (the last section).

**Conventions.** est. marks an estimate. *Check* marks a fact the sources
don't settle. Each one is settled on the first guest (GA1) before the code
that depends on it. Garuda's facts come from its own sources, read
2026-11-14, and no ISO has been downloaded yet:

- the garuda-tools ISO builder (`gitlab.com/garuda-linux/tools/garuda-tools`:
  `lib/util-yaml.sh` writes the Calamares config, `lib/util-iso.sh` the
  image);
- the `pkgbuilds` repository (`garuda-dracut-support`, `snapper-support`,
  `garuda-hooks`, `garuda-update`, `calamares-garuda`);
- the new mkosi `image-builder` and its docs;
- `iso.builds.garudalinux.org` and the 260819 release post (forum t/48606).

Code references are to local `main` at 6a78448.

## Summary

- Garuda Linux becomes a supported system for bko-holt, with one QEMU guest,
  `garuda`. It is installed from Garuda's own dr460nized live ISO, the way its
  Calamares installs offline, with `make-arch.sh --live garuda`. Then come a
  golden plan, the Test2 entries, a SticTest1 entry and HoltTest1 runs on
  Chopin and Cinqcent.
- Almost everything is built already. GRUB with `grub-mkconfig` and an ESP at
  /boot/efi is Manjaro's. The live ISO's squashfs layers are Manjaro's way
  (garuda-tools is a manjaro-tools fork). dracut named after the kernel's
  package, rebuilt by `dracut-rebuild`, is EndeavourOS-with-GRUB's.
  Snapper snapshots nested in `@` are CachyOS's (the Thinkpad's BackShot1 of
  2026-11-14) and the shared-snapshot restore of
  `docs/design/btrfs-snapshots.md`.
- What's new:
  - Garuda's dracut package (`garuda-dracut-support`) has to choose the
    `dracut-rebuild` arm that only `eos-dracut` chooses today;
  - grub-btrfs adds a snapshot menu to `grub-mkconfig`'s output;
  - snap-pac takes a pre/post snapshot on every pacman transaction, so the
    guest has real snapshots of its own from its build and its updates;
  - chaotic-aur and the `[garuda]` repo, both on by default, signed by the
    Chaotic-AUR key.
- ~2.3 d (est.) to "done", plus an optional ~0.75 d for BackShot1.

## Why

- **A popular Arch derivative with a different boot stack.** It is the
  first Arch-based guest with all of these together: GRUB on btrfs, dracut,
  and snapper with grub-btrfs. Manjaro has GRUB and btrfs with mkinitcpio and
  no snapshots. EndeavourOS-grub has dracut on ext4. CachyOS has snapper
  with Limine.
- **Snapshots that grow by themselves.** snap-pac turns every update into
  two snapshots. So a guest that is only updated, as `vm-update` updates
  every guest, builds up the snapper families that `load`'s shared-snapshot
  restore is for. Tumbleweed has them only through HoltTest1's
  `HT_SOURCE_PREP`.
- **Seven subvolumes and zstd compression**: `@ @home @root @srv @cache @log
  @tmp`, `compress=zstd`, `noatime`. No guest has a compressed btrfs root
  yet.

## What Garuda is (as far as bko cares)

| | Garuda (260819, "Temeraire") | Nearest supported system |
|---|---|---|
| Release | Rolling, with dated ISO refreshes. **260819** is what garudalinux.org links (`/iso/latest/garuda/dr460nized/latest.iso` redirects to it). garuda-tools refreshes 261110/261111 exist but aren't linked | Manjaro |
| os-release | `NAME="Garuda Linux"`, `ID=garuda`, `ID_LIKE=arch`, `BUILD_ID=rolling`, no `VERSION_ID`. `garuda-hooks` re-applies these with sed after every `filesystem` upgrade | EndeavourOS, Manjaro (version `""`) |
| ISO | `garuda-dr460nized-linux-garuda-260819.iso`, 3.32 GB, sha256 `3dac6b04225fe9577ea0143a1c712e13316dff26985c2c4417f8c5e4af77f7f7`. **No GPG signature**: only `.sha1`, `.sha256`, `.torrent`, `.zsync`, `.pkgs.txt`, all over HTTPS from the same host | Manjaro (signed) |
| Live layers | `/garuda/x86_64/{rootfs,desktopfs,ghtfs,livefs}.sfs`. Calamares's unpackfs copies `rootfs.sfs`, then `desktopfs.sfs`. `ghtfs` (drivers) and `livefs` stay on the ISO | Manjaro (`rootfs`, `desktopfs`; `mhwdfs`) |
| Partitions | GPT; ESP at **/boot/efi** (`umask=0077`), at least 260 MiB (*check* the size: Calamares's default is 300 MiB); swap `none` by default (zram from `garuda-common-settings`, `zram-size=ram`, zstd) | Manjaro |
| btrfs | `@` /, `@home`, `@root` /root, `@srv` /srv, `@cache` /var/cache, `@log` /var/log, `@tmp` **/var/tmp**. Options `defaults,noatime,compress=zstd`. /tmp is tmpfs only on an SSD (Calamares's rule) | Manjaro (4 subvolumes, `compress=zstd:1`); CachyOS (7) |
| Kernel | `linux-garuda` (7.2, CachyOS-based): `/boot/vmlinuz-linux-garuda` | Arch |
| Initramfs | dracut through `garuda-dracut-support` (it conflicts with mkinitcpio): `/boot/initramfs-linux-garuda.img` (host-only) and `-fallback.img`, rebuilt by **`dracut-rebuild`**. `05-garuda.conf`: `compress=zstd early_microcode=yes use_fstab=yes hostonly=yes hostonly_cmdline=no`. Its own module `50btrfs-snapshot-overlay` puts a tmpfs over a read-only snapshot root | EndeavourOS with GRUB (`eos-dracut`, same command name) |
| Loader | GRUB x86_64-efi, `--bootloader-id=Garuda` (capital G: `EFI/Garuda/grubx64.efi`), with the fallback `EFI/boot/bootx64.efi`. `GRUB_DISTRIBUTOR="Garuda"`, os-prober on, Garuda's theme. `garuda-hooks` re-runs `grub-install` on every grub upgrade | Manjaro |
| Snapshots | snapper, snap-pac, grub-btrfs, `snapper-support`, btrfs-assistant. Calamares's postcfg runs `snapper --no-dbus -c root create-config /`, so `.snapshots` is a subvolume **nested in `@`** (no `@snapshots`). Template "garuda": timeline off, `NUMBER_LIMIT=10`. Services: `snapper-cleanup.timer`, `grub-btrfs-snapper.path`. 260819 takes no snapshot at install | CachyOS (the Thinkpad), Tumbleweed (`@/.snapshots` mounted) |
| Repos | `[garuda]`, `[core]`, `[extra]`, `[multilib]`, `[chaotic-aur]`, all on. `chaotic-keyring` and `chaotic-mirrorlist`; Chaotic-AUR key `EF925EA60F33D0CB85C44AD13056513887B78AEB` | CachyOS (its own repos and keyring) |
| Updates | `garuda-update`: it updates itself, fetches hotfixes, re-ranks mirrors (`--skip-mirrorlist` skips that), installs the keyrings first, and answers conflict prompts through expect. `--noconfirm` makes it non-interactive | Omarchy (`omarchy update`) |
| Services | NetworkManager, avahi, bluetooth, ModemManager, timesyncd, systemd-oomd, power-profiles-daemon, plasmalogin; the btrfs maintenance timers; `garuda-pacman-lock` | |

**Not ours to follow: the image-builder ISOs.** Garuda's new mkosi builder
already publishes test ISOs (`/iso/image-builder/<edition>/latest/`, cosign
keyless signatures). They are a different system:

- erofs and dm-verity layers;
- systemd-boot with unified kernel images, and no GRUB;
- an ESP at /efi (4 GiB recommended);
- a "Fresh install" snapshot.

They have no `release/` directory yet. When one is released, Garuda's default
install changes. That is a second guest then (Out of scope), not a change to
this one.

## What already works, and what doesn't

**bko-holt `dump`:**

- `osrel.rs` `SUPPORTED` has no `garuda` row, so `check_supported` refuses
  it. It needs `("garuda", "", "Garuda Linux")`, as EndeavourOS and Manjaro
  have, with a unit test from the os-release above and the refusal list
  (`supported_names()`) updated.
- `installed_packages()` reads pacman's database already.
- `PACKAGES` (`boot.rs:213`) lacks these names. `testvm/update/fingerprint.py`
  mirrors the list, and a test checks that the two agree.
  - `garuda-dracut-support` is the one that matters: load keys on it.
  - `linux-garuda` is the kernel.
  - `grub-btrfs`, `snap-pac` and `snapper-support` say how snapshots reach
    the boot menu.
  - `snapper` is listed already.
- Generator detection (`initramfs_json`): mkinitcpio isn't installed (it
  conflicts), so `dracut`. *Check* that no mkinitcpio is left on the
  installed system: a stray one would win, as it is tested first.
- Kernels: `/boot/vmlinuz-linux-garuda` with `initramfs-linux-garuda.img` and
  `-fallback.img`, the same names as Manjaro's and EndeavourOS-grub's.
  *Check* that `kernels_json()` pairs the fallback with its kernel as it does
  for those.
- btrfs: seven subvolumes plus `.snapshots` and its snapshots, nested and
  unmounted. The backup holds them under `/.snapshots/N/snapshot` (no
  `--one-file-system`). `shared.rs`'s `snapper_slot("@/.snapshots/N/snapshot")`
  finds them, with `@` (mounted at /) as the family's anchor: the CachyOS
  case, which BackShot1 passed with six snapshots on 2026-11-14.

**bko-holt `load`:**

- **Initramfs.** `initramfs()` (`load/commands.rs:2683`) runs
  `dracut-rebuild` only when the dump has `eos-dracut`. Otherwise it runs
  the generic `dracut --force --kver K` loop. Here that loop would write
  `/boot/initramfs-7.2.x-garuda.img`, and leave the source's
  `initramfs-linux-garuda.img` (the one grub.cfg boots) stale. The change is
  one condition: `eos-dracut` **or** `garuda-dracut-support` → `chroot T
  dracut-rebuild`, plus a unit test beside EndeavourOS's in
  `arch_and_fedora_boot_tools`.
  - *Check* that Garuda's command really is `dracut-rebuild` on the
    installed system.
  - *Check* that it makes both images in the chroot. Host-only from the
    rescue system is what EndeavourOS-grub already does, on the same
    virtual hardware.
- **GRUB.** The UEFI arm already does `grub-install --target=x86_64-efi
  --efi-directory=/boot/efi --bootloader-id=ID --no-nvram`, then
  `grub-mkconfig -o /boot/grub/grub.cfg` with os-prober bound to `true`.
  The ID comes from the ESP when the NVRAM has no entry (`bootloader_id`,
  `commands.rs:2840`).
  - *Check* that it picks `Garuda`, case kept, and not `boot`. Manjaro's
    `EFI/manjaro` + `EFI/boot` gave `manjaro`.
- **grub-btrfs.** `grub-mkconfig` runs `/etc/grub.d/41_snapshots-btrfs`. It
  lists the restored snapshots into `/boot/grub/grub-btrfs.cfg`, through
  `btrfs` and `grub-probe` in the chroot.
  - *Check* that it doesn't fail, and doesn't stall, in the chroot. Its
    `grub-btrfs-snapper.path` unit isn't running there.
  - If it fails, load has two ways out: bind it to `true` as for os-prober,
    or set `GRUB_BTRFS_ENABLE=false` for the one run. Either way it has a
    note that the snapshot menu comes back with the first snapper change
    after boot (the path unit remakes it).
- **Snapshots.** Nothing new: `--btrfs-snapshots shared` (the default) as
  for CachyOS and Tumbleweed. Garuda's snapshots are pre/post pairs from
  snap-pac, read-only, with no rollback in a fresh guest.
- **No other plan changes.** Every arm added is keyed on Garuda's own
  package or ID.

**The rescue image:** no change ([[one-kernel-rescue]]). The rescue system
has btrfs and mounts `compress=zstd` as it is.

## The guest

One guest, `garuda`, built by `make-arch.sh --live garuda ISO 2026.10.01
WORK_DIR`. As for the other `--live` guests, the Arch ISO boots and runs
`arch/install-live.sh`, with Garuda's ISO as the second CD. The initramfs is
then built on the guest's own virtual hardware. UEFI, `--uefi
--no-secure-boot`.

`install-live.sh` gains a `garuda` arm that does what Calamares does:

1. GPT; an ESP of 300 MiB (*check*) at /boot/efi, `umask=0077`; btrfs on the
   rest with the seven subvolumes, mounted `defaults,noatime,compress=zstd`.
   No swap partition.
2. Copy `/live/garuda/x86_64/rootfs.sfs`, then `desktopfs.sfs`, as for
   Manjaro.
3. Write fstab as Calamares does: seven btrfs lines and the ESP. A tmpfs
   /tmp line only for an SSD, and the virtio disk reads as rotational (*check*:
   `/sys/block/vda/queue/rotational`).
4. Locale, hostname and machine-id, as now. `pacman-key --init` and
   `--populate archlinux chaotic` (*check* the keyring names under
   `/usr/share/pacman/keyrings`).
5. Copy each `usr/lib/modules/*/vmlinuz` to `/boot/vmlinuz-<pkgbase>` and run
   `dracut-rebuild`, as EndeavourOS-grub does.
6. Put the serial console in `GRUB_CMDLINE_LINUX_DEFAULT`. Then
   `grub-install --bootloader-id=Garuda --no-nvram`, the fallback
   `EFI/boot/bootx64.efi`, and `grub-mkconfig` with os-prober off. Check that
   grub.cfg names `subvol=@` and `initramfs-linux-garuda.img`.
7. Calamares's postcfg: `snapper --no-dbus -c root create-config /` with
   Garuda's template. *Check* the rest of its shellprocess list in
   `calamares-garuda`: its live-user removal, its service enables and its
   Garuda-specific patches.
8. As every guest has: SSH, root and `tester`, `multi-user.target`, the
   caches emptied. Any pacman run in the chroot (openssh, if
   dr460nized lacks it) fires snap-pac, so the guest may start with a
   pre/post pair. That's welcome, and it's recorded in WORK_DIR.
9. Check that os-release says `ID=garuda`, as the script already does.

Other changes:

- `make-arch.sh`: `--live` takes `garuda`, with `DISK_SIZE=32G`
  (Calamares's own check asks for 29.9 GB; it's sparse, so it costs
  nothing).
- `rebuild-one.sh`: a `garuda` line.
- `/bkoscratch/rebuild-inputs/garuda/` on Chopin and Cinqcent: the ISO, its
  `.sha256` and `.pkgs.txt`. With no signature to check, the sha256 above is
  pinned in `rebuild-one.sh`'s line and checked before the build. Only those
  two hosts get it: the small hosts' 10G RAM disk can't hold a run (below).
- `vm-update` (`update/in-guest.sh`, `# t2-section: Linux.pacman`):
  `chaotic-keyring` joins the keyrings that are refreshed first. snap-pac
  then makes a pre/post pair per update, and the cleanup timer keeps 10.
  *Check* that plain `pacman -Su --noconfirm` gets through Garuda's
  replaces and conflicts (Decision 3).
- `holttest1d/ram-sizes.tsv`: est. D 22, MEM 4 until a run measures it.
  EndeavourOS's 24 GiB guest peaked at 14.5 GiB, and dr460nized is about 1.5×
  its data, though zstd shrinks what the qcow2 holds. That fits a big
  host's ceiling (29), not a small host's (9).
- GuestTest2 (`t2lib/guest_judge.py`): a source rule that
  `/etc/pacman.d/chaotic-mirrorlist` has a `Server`, as EndeavourOS has one
  for its own mirrorlist. `K-pac` covers the kernels.
- The guest gets a GuestTest1 grade.

## What GA1 found

Settled on 2026-11-14 from the ISO itself: its Calamares config in
`livefs.sfs`, `rootfs.sfs` and `desktopfs.sfs`, and Calamares's own
`postcfg` module. Also from the `garuda` guest's build and its first updates
on Chopin.

**The ISO.** The direct server ran at about 4 KB/s, so the ISO came from
Garuda's R2 mirror (`r2.garudalinux.org`, the same path). It is 3,559,471,104
bytes, and its sha256 matches the pin. It is kept in
`/bkoscratch/rebuild-inputs/garuda/` on Chopin and Cinqcent with its
`.sha256` and `.pkgs.txt`. The Arch ISO 2026.10.01 and its signature are
hard links from `rebuild-inputs/arch`.

**The layers.** rootfs is 2.9 GB and desktopfs 5.1 GB, uncompressed.

**The ESP.** `partition.conf` sets no ESP size, only a 260 MiB minimum. So
Calamares's 300 MiB, at /boot/efi.

**What Calamares runs.** The exec sequence ends with `services-systemd`,
`dracutlukscfg`, `postcfg`, `grubcfg`, `bootloader`. There is no shellprocess.

**`postcfg` does these steps, in order:**

1. `pacman-key --init` and `--populate`. The keyrings are archlinux and
   chaotic; Garuda has none of its own.
2. Removes the other CPU vendor's microcode with `pacman -R`. Chopin's CPU is
   AMD, so `intel-ucode` goes. Garuda's `/etc/pacman.d/hooks/90-dracut-install.hook`
   then runs `dracut-install-garuda` for all kernels. That rebuilds
   `/boot/initramfs-linux-garuda.img` (host-only) and `-fallback.img`, and
   dd-copies `vmlinuz-linux-garuda` again.
3. Copies `/etc/skel` to /root.
4. `snapper --no-dbus -c root create-config /`. This refuses: "Creating
   config failed (subvolume already covered)", because the image already
   ships `/etc/snapper/configs/root`. Calamares ignores its exit status.
5. `btrfs subvolume create /.snapshots`.

**`dracut-rebuild`** is `echo rebuild | dracut-install-garuda`. It covers only
the `/usr/lib/modules/*/pkgbase` files that pacman owns (`pacman -Qqo`).

**snap-pac in the install.** Its pre hook has `AbortOnFail`, but with no
`/.snapshots` it prints snapper's error and still lets the removal go on.
So a fresh guest has no snapshots, as the spec said.

**The other checks:**

- There is no mkinitcpio. The image does have
  `/etc/pacman.d/hooks/90-mkinitcpio-install.hook`, which only calls
  `garuda-hooks-runner kernel` and does nothing when dracut's hook is there.
- openssh is installed already.
- `/etc/default/grub` has `GRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 quiet"`,
  os-prober on, and the dr460nized theme.
- `41_snapshots-btrfs` exits 0 when it finds no snapshots: its
  `print_error` does `exit 0`.
- The virtio disk reads as rotational, so there is no tmpfs `/tmp` line in
  fstab.
- Every unit in `services-systemd` exists except `pacman-init`, which is the
  live ISO's.

**The build** took 391 s (`rebuild-one.sh garuda` under `cmp-run.sh
garuda-rb 1 garuda=14%5`, REBUILD_DIR `/mnt/bko-ram/garuda-rb`). The image is
5.4 GiB, sha256 `af777916…`. It boots alone with systemd running, no failed
unit, and SSH. The first try had stopped on create-config's refusal, which
the script now allows, as Calamares does.

**`vm-update --try`** was ok: 481 s, overlay 7,638 MiB, 695 packages,
`linux-garuda` 7.3.4, grub 2.16. Plain pacman did it, with `chaotic-keyring`
refreshed first; no conflict and no replace stopped it. snap-pac made
snapshots 1-4: pre/post for the keyrings, then for the upgrade. Garuda's
hooks then did three things:

- rebuilt both images;
- re-ran `grub-install --bootloader-id=Garuda`
  (`garuda-hooks-runner grub-update`);
- ran `update-grub`. grub-btrfs listed `@/.snapshots/1..3/snapshot` in the
  boot menu.

vm-mount mounts all 8 file systems read-only.

**The first real update** then merged on Chopin: image-state generation 1,
sha256 `0809203a…`. Since 2026-11-13 Chopin is the only fleet host, so the
update went to no other host. Cinqcent got a full copy by hand, with the
same sha256.

Its fingerprint dump failed with exit 3. The guest has the shipped bko-holt,
which doesn't know Garuda. It comes with the next holt-bin release.

## What GA4 found

2026-11-14, HoltTest1 on both hosts. Every run used:

- bko-holt 65e2cffec80d, built on Chopin (static, Rust 1.94.1) from this
  working tree's holt/, which includes BigLinux's uncommitted `bootloader_id`
  change that keeps the ESP directory's case;
- `HT_PORT` 2694 or 2695.

**rel6's rustic-bko restores the first snapshot wrong.** The shipped
rustic-bko is 0.11.4+backotter.9.

- Its run, `ga4-2` (Cinqcent), passed every load stage and the boot. It
  failed fsmeta on 24,630 files whose content differs in
  `.snapshots/1/snapshot`, the first shared snapshot.
- That is the `--clone-from` bug found on the CachyOS Thinkpad. It is fixed
  in 0.11.4+backotter.11 (rustic_core 3f2f3b2, rustic a1ec9bf).
- Later runs use `HT_ROUND=t2r-58a6955-3bb01f1-99b9b56`, whose rustic-bko is
  .12. Until a shipped set has the fix, a snapper source needs that round.
  This is in HoltTest1's README row.

**Two runs failed for reasons outside Garuda:**

- **`ga4-1` (Chopin):** its restore stage died with exit 255 on a full RAM
  disk. Another session's finished build had stayed in /mnt/bko-ram after
  its reservation ended. That session moved it, and the run was within its
  own reservation.
- **`ga4-3` (Cinqcent):** every check passed, then `ht1.sh: line 1134:
  syntax error`. A sync had replaced `ht1.sh`, carrying another session's
  uncommitted edits, under the running script. Don't sync while an ht1
  run is going.

**One judge rule.** The boot stage's `dracut-rebuild` dd-copies each
kernel to `/boot/vmlinuz-PKGBASE` again. So `boot/vmlinuz-*` may change
mtime and atime, not content, mode or owner (`judge.py`, with two
judge-test cases).

**The passes:**

- **`ga4-4` (Chopin) and `ga4-5` (Cinqcent):** every load stage, the boot
  alone, 0 fsmeta differences, and 639 paths from the boot stage. snap-pac's
  four snapshots are restored as a chain from `@`. The boot stage runs
  `chroot T dracut-rebuild`, then
  `grub-install --target=x86_64-efi --efi-directory=/boot/efi
  --bootloader-id=Garuda --no-nvram`. grub-btrfs lists the restored
  snapshots. RAM-disk peaks were 17.2 and 16.6 GiB, so `ram-sizes.tsv` has
  D 20, MEM 4.
- **`ga4-6` (Chopin) and `ga4-7` (Cinqcent), with
  `HT_SOURCE_PREP=prep/garuda-snapper.sh`:** 8 snapshots, one of them
  read-write, all PASS. After the first snapshot each restore wrote KiB to
  MiB and reused 7.9-8.3 GiB of existing contents. The read-write snapshot
  made last on the source (7) has ID 272 here, 270 on the source, as
  btrfs-snapshots.md allows when a subvolume comes after it.
  - ht1.sh's extents-shared check skips every snapshot, because no mount
    reaches it on the source. So the sharing is shown by load's own
    numbers, not by `snapper list`'s exclusive sizes: Garuda's snapper
    config has no qgroup.
  - Prep runs peaked at 17.7 and 19.0 GiB, with D 19-20 reserved. A prep run
    wants `--reserve 22`.

## SticTest1 / SticTest2

The archivers are static binaries, and EndeavourOS and Manjaro have no
SticTest1 entry. Garuda would add something that no entry covers today: a
btrfs mounted `compress=zstd`.

The proposal (Decision 6) is `testvm/stictest1/garuda`, from `tumbleweed`'s
design (btrfs subvolumes, `bkoshim.py`). It adds three things:

- **Compression:** a fixture file written compressed. Its `btrfs.compression`
  property and `c` flag must round-trip, or be classed as LIMITs.
- **A nested, unmounted snapshot:** it must restore as a directory tree
  with its contents.
- **Times on `noatime`:** atime judged as on Garuda's mounts.

Then `stic/manifests.json` and `limits.json` get the entry, and
`check-manifests.py`'s count goes up by one. SticTest2 picks it up from the
manifest.

## HoltTest1

- **Group:** beside EndeavourOS and Manjaro, the same as them in both places
  (Decision 5):
  - HoltTest1's README row "(1All only)", which becomes "EndeavourOS,
    Manjaro, Garuda Linux";
  - Test2's family `A` in `classes.json`.
- **Runs:** one plain `ht1.sh garuda` on each big host. Then one with
  `HT_SOURCE_PREP=prep/garuda-snapper.sh` on each (Decision 4).
- **`prep/garuda-snapper.sh`:** a few small pacman transactions, so that
  snap-pac makes pairs, plus `snapper create` with file changes between them,
  as `tumbleweed-snapper.sh` does. It has no rollback: Garuda boots
  `rootflags=subvol=@`, so `snapper rollback`'s default subvolume never
  takes effect, and Garuda's own restore (btrfs-assistant renaming
  subvolumes) is out of scope.
- **Passing** has three parts:
  - every load stage passes, the guest boots alone, and there are 0 fsmeta
    differences;
  - `snapper list` on the restored guest shows exclusive sizes within a
    small factor of the source's;
  - `load.log` has no `AlreadyExists`, and grub.cfg (with grub-btrfs.cfg)
    lists the restored snapshots.
- **`judge.py`:** `^boot/efi[^/]*/EFI(/|$)` already covers
  `boot/efi/EFI/Garuda` and `EFI/boot`. `^boot/grub2?(/|$)` covers
  `grub-btrfs.cfg`. `^boot/(…|initramfs-[^/]+|…)$` covers both images.
  `(tmp|var/tmp)$` covers @tmp's root directory. New rules come only for
  paths the first run shows, with judge-test.py cases. *Check* in
  particular:
  - dracut's `var/lib/dracut`;
  - anything `garuda-hooks` or `50btrfs-snapshot-overlay` writes at the
    first boot.

## Test2

As for Gentoo's G2b:

- `holt/tests/corpus/garuda/` (`src.json`, `plan.txt`) from the passing
  run's dump, a row in `index.tsv`, and a paragraph in the corpus README;
- `classes.json` (family `A`, `uefi`), `holt/spec.json`, `guest/spec.json`,
  `estimates.tsv` (`guest:garuda`, `holt:garuda`);
- `t2lib/plan.py`'s counts, `tests/fixtures/p2/world.py`, and the goldens
  re-blessed.

Done when two corpus builds have the same sha256, the leak scans are 0, no
other plan changes, and Test2's tests pass.

Those files are modified in the working tree right now by the cachyos-zfs
session. The BigLinux session will touch the same ones. GA3 goes after
them, or is merged with them by line, never by overwriting the file.

## BackShot1 (optional, later)

The Thinkpad runs CachyOS now.

- **Rehearsal:** `bs1-qemu.py --virtio` on the `garuda` guest, on both
  hosts. It needs no fix that the table shows:
  - `bs1-adopt` installs with pacman, as for any Arch system;
  - `bs1-manifest` knows `.snapshots/N`, and already expects
    `boot/initramfs-*`, `boot/grub/*` and `efi/*`;
  - the snapper-timer hold (93978fd) covers `snapper-cleanup.timer`.
- **What to watch:** `grub-btrfs-snapper.path` remakes `grub-btrfs.cfg` when
  a snapshot changes. bs1-adopt's own pacman run makes a snap-pac pair before
  phase 1, which is source state, as on Gentoo.
- **A real run:** only if the user reinstalls the Thinkpad with Garuda
  (dr460nized, the installer's defaults). That is the user's call and the
  user's install. It joins `bs1-all.md` as one more distro.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| GA1 | The guest: `install-live.sh`'s `garuda` arm, `make-arch.sh --live garuda` with `DISK_SIZE=32G`, the rebuild inputs on Chopin and Cinqcent with the pinned sha256, the `rebuild-one.sh` line, `chaotic-keyring` in `in-guest.sh`, the `ram-sizes.tsv` row. Settle every *check*: the ESP size, keyring names, `dracut-rebuild` and both images, the ESP's directory names, the rotational disk and /tmp, postcfg's other steps, no mkinitcpio, the first snapshots. Done when it is built on Chopin, copied to Cinqcent with the same SHA-256, boots alone on both, and `vm-update --try` is ok (it shows a snap-pac pair) | 0.75 d |
| GA2 | bko-holt, by a High-effort subagent: the `SUPPORTED` row and its tests, `PACKAGES` and `fingerprint.py`, the `dracut-rebuild` arm for `garuda-dracut-support` and its unit test, and grub-btrfs's way out if GA1 shows one is needed. Done when `cargo test` passes and the guest's dump plans `dracut-rebuild`, `--bootloader-id=Garuda` and a shared snapshot for each snap-pac snapshot | 0.25 d |
| GA3 | The golden plan and the Test2 entries (above), and GuestTest2's chaotic-mirrorlist rule. Done as Gentoo's G2b was | 0.35 d |
| GA4 | HoltTest1: the plain runs and `prep/garuda-snapper.sh` runs on both hosts, the judge rules the runs show (with judge-test.py cases), the README row, and the measured `ram-sizes.tsv`. Done when all four runs PASS with the snapshot checks above | 0.5 d |
| GA5 | SticTest1 `garuda` (Decision 6) with both archivers on both hosts. Done at 0 FAIL, the LIMITs classed | 0.35 d |
| GA6 | Docs: holt/README.md's list and tested-by, `osrel.rs`'s comment, the HoltTest1, SticTest1 and Test2 READMEs, `layload.md`'s boot stage (the `dracut-rebuild` condition), memory | 0.1 d |
| | *≈ 2.3 d in all* | |
| GA7 | Optional: BackShot1 rehearsals on `garuda` on both hosts (0.25 d); a Thinkpad run if the user installs Garuda there (+0.5 d) | (0.75 d) |

Every source change is made by a High-effort subagent. Commits go on main,
only when the user asks, and hold only this work's files.

**Costs** (est.):

| # | Host time | Disk | The user's time |
|---|---|---|---|
| GA1 | a 20-30 min build on Chopin (WORK_DIR in /mnt/bko-ram, `--reserve 14 5`: 3.3 GB of squashfs unpacked, dracut twice), the copy to Cinqcent | ISO 3.3 GB a host in rebuild-inputs; qcow2 ~8-11 GiB a host | none |
| GA4 | ~1-1.5 h of queue a host for two runs at D ~22: one run per big host at a time beside small jobs | update overlays ~1-2 GiB (snapshots keep old packages) | none |
| GA5 | ~10 min a host | small | none |
| GA7 | ~40 min a host for the rehearsal | — | the Thinkpad's reinstall, if chosen |

## Risks

- **No signature on the ISO.** The pinned sha256 shows that the ISO is the
  one read on 2026-11-14 from Garuda's own host. It doesn't show that Garuda
  made it. That is weaker than Manjaro's or Gentoo's signatures, and it's
  said so in `rebuild-one.sh`.
- **A large guest.** If the RAM-disk peak passes the big hosts' 29 GiB, the
  remedy is kde-lite (2.12 GB, the same installer and layout; Decision 1).
  Running from `/bkoscratch` breaks [[work-in-ram]].
- **Chaotic-AUR churn.** A rolling third-party repo can stop `pacman -Su` on
  a conflict or a replace. The update names the package, and `in-guest.sh`
  gets an arm for it, as EndeavourOS got for Plasma 6.8. If that recurs,
  switch to `garuda-update --noconfirm --skip-mirrorlist` (Decision 3).
- **grub-btrfs in the chroot** may fail or hang. It has a way out in GA2,
  with a note.
- **snapper-cleanup.timer during a HoltTest1 backup** could delete a
  snapshot mid-run, as it did on the CachyOS Thinkpad. *Check* whether
  ht1.sh's source backup needs BackShot1's timer hold.
- **Garuda switches to image-builder** (systemd-boot, UKIs, ESP at /efi).
  Then a fresh install no longer matches this guest. The guest stays
  valid for installs made from 260819-era ISOs, and the new layout is a new
  guest.

## Out of scope

- The image-builder (mkosi) installs: systemd-boot with unified kernel
  images, erofs/dm-verity live layers, ESP at /efi. That would be a later
  `garuda-sdboot` guest, est. +0.75 d. Today, load would refuse UKIs, as on
  Gentoo.
- Garuda's restore of a snapshot through btrfs-assistant (renaming `@` and
  promoting a snapshot), and booting a snapshot via grub-btrfs's overlay.
  Restoring a system that was *in* that state is a later case.
- Other editions (mokka, gaming, sway, hyprland, xfce-lts…). They share the
  installer and layout, and differ in packages.
- LUKS (Calamares's "encrypt system"), a swap partition (`suspend`), and BIOS
  installs: the ISO checks for EFI.
- Garuda Nix (a NixOS system), which is a different distribution.

## Decisions for the user

1. **Edition and ISO.** dr460nized **260819**, the ISO garudalinux.org
   links today (recommended): Garuda's flagship and what most people install,
   from the tagged release. The other choices:
   - the newer garuda-tools refresh 261111: unlinked, with no release post;
   - kde-lite 260819: 2.12 GB, the same installer and layout, the fallback
     if dr460nized outgrows the RAM disk;
   - xfce, which ships the LTS kernel.
2. **Trusting an unsigned ISO.** Pin its sha256 and keep the ISO raw in
   `rebuild-inputs` on both hosts (recommended: it's all Garuda publishes).
   Or wait for an image-builder release, whose cosign signatures can be
   checked.
3. **Updates in `vm-update`.** Plain `pacman -Sy` of the keyrings
   (`chaotic-keyring` added), then `pacman -Su --noconfirm` (recommended: the
   same path and output as every other pacman guest, so GuestTest2's known
   lines hold). Or `garuda-update --noconfirm --skip-mirrorlist`: Garuda's
   supported path, which handles its hotfixes and replaces, but has its own
   output and self-update.
4. **Snapshots in HoltTest1.** The guest's own snap-pac pairs in every run,
   plus one `HT_SOURCE_PREP=prep/garuda-snapper.sh` run a host with more
   snapshots and changes, and no rollback (recommended). Or the plain runs
   only. Or also a btrfs-assistant-style restore of a snapshot as the source
   (est. +0.5 d: new ground for `shared.rs`, whose live root would then be a
   snapshot of a slot inside a renamed subvolume).
5. **HoltTest1's group.** The same place as EndeavourOS and Manjaro: the
   README's "(1All only)" row, and Test2 family `A` (recommended). Or a named
   group, **1Arch**, holding `arch`, `cachyos`, `endeavouros*`, `manjaro`,
   `omarchy`, `garuda`, which moves rows that other sessions own. Or 1A by
   name.
6. **SticTest1.** A `garuda` entry with the compression, nested snapshot and
   `noatime` checks (recommended, 0.35 d: the first compressed btrfs in
   SticTest1). Or none, as for EndeavourOS and Manjaro.
7. **BackShot1.** Rehearse on the guest only for now (recommended). Garuda on
   the Thinkpad is the user's choice of reinstall, later, through
   `bs1-all.md`.
8. **A supported system** in bko-holt's `SUPPORTED`, named in its messages
   (recommended). Or test guests only.
