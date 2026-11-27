# SteamOS 3 support (A/B slots on GPT partlabels)

2026-11-27. Asked for by the user: prepare support for SteamOS 3
(DistroWatch #46). SteamOS is Valve's Arch-based image system. It keeps
two whole root slots and switches between them on updates. This spec
follows Vanilla OS's (docs/design/vanillaos.md, the A/B precedent) and
Bazzite's (docs/design/bazzite.md, the BackShot1 plan for an image
system). It borrows Ubuntu Core's fixed partition roles
(`holt/src/snapcore.rs`). It is a spec only: nothing is built yet, and it
waits for the user's approval (the last line).

**Conventions.** est. marks an estimate. *Check* marks a fact the sources
don't settle. Each one is settled on the first guest (S1) or on the A100
(S10), before any code that depends on it.

SteamOS facts come from Valve's public mirrors at github.com/evlaV, read
2026-11-27. Nothing was installed or run. The abbreviations used below:

- `sc:` is `steamos-customizations` master (tag `jupiter-20261027.1`).
- `sc39:` is that repo's branch `jupiter-3.9.x` (= `jupiter-3.9-20261021.1`), and `sc38:` is `jupiter-3.8.x`.
- `efi:` is `steamos-efi` at tag `jupiter-20261013.1` (packaged as 20261012.1): the chainloader `steamcl`, `bootconf`, `steamcl-install`.
- Every evlaV clone is a shallow mirror of one squashed commit, so a commit's date is its author date, not when a change landed.
- `holo:` is `holo` (PKGBUILDs and patches, main, 2026-11-24).
- Also read: `atomupd-daemon`, `steamos-atomupd`, `jupiter-hw-support`, `dirlock`, `holo-dirlock-extras`, `steamos-reset`, and the community teardown `randombk/steamos-teardown` (2022-24).

`repair:` is Valve's installer script `repair_device.sh`, build 20260707.10. No Valve package ships it, so it was read from a third-party verbatim copy, github.com/louij2/steamos_custom_install `upstream/repair_device.sh` (484 lines; authenticity *check*). Its partition types and labels match the GPT that I parsed myself from the recovery image.

Other sources read:

- **Image data:** the update metadata at `https://steamdeck-atomupd.steamos.cloud/meta/holo/steamos/amd64/steamdeck/{stable,beta,preview,main}.json`, and the `manifest.pacman` package lists of builds 20261023.100 (3.9.5) and 20261124.1 (3.9.7).
- **Image headers:** the first 338 MB of the 3.8.14 recovery image, the first 4 MB of the 3.8.14 system image and the first 1 MB of 3.9.5's build image. I parsed their GPT and filesystem headers with my own scripts. Nothing from the images was executed.
- **Valve's EULA** for the images, on https://store.steampowered.com/steamos/download/?ver=steamdeck (`steamos/dl-steamdeck.html` in the scratchpad).
- **Kernel config:** linux-neptune-72 7.2.15's `.config`, from its headers package (the 3.9.7 kernel).
- **Valve's pages:** the FAQ "SteamOS Installation and Repair" (https://help.steampowered.com/en/faqs/view/65B4-2AA3-5F37-4227) and the release notes (Steam News, appid 1675200).
- **Prior art for QEMU:** Igalia's recipe (https://blogs.igalia.com/berto/2022/07/05/running-the-steam-decks-os-in-a-virtual-machine-using-qemu/, updated July 2025), `cilki/goldboot`, and `cchulo/ally-x20-steamos`.

Code references are to local `main` at d4aea22, read in its working tree, and re-checked at 1d377cb (the commits between touch only docs and ht1-bsd, so no cited line moved). The uncommitted edits in the tree are in files this spec only cites. Copies of the sources read are in the session's scratchpad, not in the repo; the image-header parses are in its `review/sys/` (its `steamos/syshead` and `steamos/sysimg` are empty).

## Summary

- **Target SteamOS 3.9, the current stable (3.9.5, build 20261023.100).** Support it through one os-release row, `steamos` `3`. That row also takes 3.8 (still the newest advertised install image) and 3.10 (preview) (Decision 1). Every install here starts from a 3.8.14 image, because Valve advertises no 3.9 install image, and an update then takes it to 3.9. (Each build directory does hold an unadvertised whole-disk image, such as 3.9.5's, in the recovery image's single-slot layout: the Install images row.)
- **The layout.** Every SteamOS install has eight GPT partitions, found by partlabel:
  - `esp`: the shared ESP, holding the chainloader `steamcl` and each slot's boot record.
  - `efi-A` and `efi-B`: vfat, type Microsoft basic data, each slot's GRUB.
  - `rootfs-A` and `rootfs-B`: btrfs, 5 GiB, zstd, made read-only by the btrfs `ro` property on the top level.
  - `var-A` and `var-B`: ext4, each slot's `/var`. The `/etc` overlay upper lives here.
  - `home`: ext4 with `casefold`, written as a 100 MiB stub and grown to fill the disk at first boot.
  - Partition sets ("partsets") on each efi partition map labels to PARTUUIDs, and the initrd finds root through them. The kernel command line has no `root=`.
- **bko-holt can't back it up today.** Nothing is wrong with the archiver. dump refuses `ID=steamos`, and three of the eight filesystems (the inactive slot) are never mounted. The read-only flag sits on the btrfs top level, which dump doesn't record and load doesn't restore (`holt/src/report/btrfs.rs:78-105`, `holt/src/load/commands.rs:6921`). And `/` has no fstab line at all.
- **The backup is all eight filesystems, read raw**, in one tree in post-install's shape:
  - The booted slot at `/`, `/var` and `/efi`.
  - The ESP at `/esp` and home at `/home`.
  - The other slot as a chroot at `/mnt`, `/mnt/var` and `/mnt/efi`.
  - dump lists all eight in `storage.backup_sources`, with mount hints for the three that nothing mounts. load mounts the same tree.
- **The boot stage regenerates nothing.** load keeps every PARTUUID, partition name, type, btrfs fsid, ext4 UUID and vfat volume ID. So the partsets, both `grub.cfg`s, the fsid embedded in each `grubx64.efi`, and `steamcl` with its confs all come back byte for byte and stay right. The stage:
  1. Checks that each slot's GRUB leads to that slot's own kernel and root, and that the partsets match the new GPT.
  2. Remakes the NVRAM entry.
  3. Writes its log.
  4. Sets `ro` back on both rootfs top levels.
- **Both slots boot after the restore.** The guest and the A100 are each updated once before any backup, so the slots hold different builds: A at 3.8.14 and B at 3.9.x. HoltTest1 and BackShot1 then boot the restored disk into each slot, switching with SteamOS's own `holo-reboot --reboot-other`. Its `boot-other` flag is sticky (`set-mode booted` never clears it), so the boot sequences and every offline slot choice take it into account.
- **Guest `steamos3`**, built from Valve's recovery image by Valve's installer, run unattended (Decision 2). HoltTest1 group 1Obscure.
- **BackShot1 on the A100 is feasible but unofficial.** Valve lists only AMD hardware. The A100 is an Intel N100 IdeaCentre all-in-one. The OS does ship i915, r8169 and nvme, and the A100's Secure Boot is already off.
  - It is installed from the BackOtter stick: a new `steamos-bootstrap.sh` writes Valve's 3.8.14 *system image* onto the disk by serial, from `mybig512:backshot1/steamos/` (Decision 9).
  - SteamOS has no LUKS, so no unlock family is needed. A home encrypted with `dirlock` (fscrypt) is refused in v1.
- ~9.0 d (est.), BackShot1 included.

## Why

- **Two whole roots, again, but found differently.** Vanilla's ABRoot finds its roots by filesystem label, and an LVM name and a master `grub.cfg` pick one. SteamOS finds everything by PARTUUID through partsets that are written onto the efi partitions. It picks a slot through per-slot boot records on the ESP, which its own UEFI chainloader reads. Nothing in bko knows either mechanism.
- **Filesystems that nothing mounts.** Three of the eight filesystems (`rootfs`, `efi` and `var` of the inactive slot) are in no mount and no fstab line. `/efi` and `/esp` are automounts that unmount after a minute idle (seen as autofs with `timeout=60`, `randombk/steamos-teardown docs/partitions.md:120-121`; what makes them is *check*, see the Mounts row). `check_coverage` (`testvm/realworld/backshot1/bs1-common:386`) would stop on the first three, and any tree built from mounts would miss them.
- **A read-only top level.** `holo-readonly enable` runs `btrfs property set / ro true` on the top level, subvolume 5 (`sc:misc/bin/holo-readonly.in:48-98`). bko lists only subvolumes 256 and up, so a restored SteamOS root would come back writable. This is a generic gap, and any system with a read-only FS_TREE has it.
- **Common hardware, common use.** SteamOS is on every Steam Deck, the Steam Machine and the Legion Go S, and Valve offers it for other AMD PCs (Valve's FAQ above). Those users have the most to lose from a reimage: a Steam library, saves and shader caches in `/home`.

## What SteamOS 3 is (as far as bko cares)

| | SteamOS 3.9.5 (stable) | Nearest supported system |
|---|---|---|
| Releases | stable 3.9.5 (20261023.100); beta 3.9.7 (20261124.1); preview 3.10.1 (20261116.100); main 3.11.0 (20261119.1000) (atomupd meta, fetched 2026-11-27). 3.8 went stable 2026-06-18 and ended at 3.8.28 (Sept 2026). 3.9 renamed the scripts from `steamos-*` to `holo-*` and kept aliases (`steamos-alias 2.0-1`; which aliases exist is *check*) | Vanilla OS 3 (A/B) |
| os-release | `ID=steamos`, `ID_LIKE=arch` (*check* the installed file, and its `VERSION_ID`, `VARIANT_ID` and `BUILD_ID`). The image identity is `/etc/steamos-atomupd/manifest.json` on each rootfs: `{"product":"steamos","release":"holo","variant":"steamdeck",...}` with its buildid and version (3.9.5's manifest). **The variant differs per slot:** both install images are OOBE builds, `"variant":"steamdeck-oobe"` (https://steamdeck-images.steamos.cloud/steamdeck/20260707.10/steamdeck-oobe-20260707.10-3.8.14.manifest.json), whose update metadata names `replacement_eol_variant: "steamdeck"` (https://steamdeck-atomupd.steamos.cloud/meta/holo/steamos/amd64/steamdeck-oobe/stable.json), and post-install writes the new variant into the new slot's upper `steamos-atomupd/preferences.conf` (`sc39:atomic-update/rauc/post-install.sh.in:244-259`). So after one update slot A (3.8.14) reads `steamdeck-oobe` and B reads `steamdeck` | Arch (`arch` row, `holt/src/osrel.rs:177`) |
| Install images | **Recovery image** `steamdeck-oobe-repair-20260707.10-3.8.14.img.bz2`, 3,357,999,306 B. `https://steamdeck-images.steamos.cloud/recovery/steamdeck-repair-latest.img.bz2` redirects (302) to it; Last-Modified 2026-07-07. It is a single-slot USB system (esp 64 MiB, efi-A 128, rootfs-A 5120, var-A 256, home 2048 MiB) that boots to a Plasma desktop as `deck` and carries `repair_device.sh` in `/home/deck/tools/`. **System image** `steamdeck/20260707.10/steamdeck-oobe-system-image-20260707.10-3.8.14.img.zst`, 3,290,798,387 B: a whole 10.97 GiB disk in Valve's 8-partition layout. **Unadvertised build images:** each build directory also holds a whole-disk image, such as `steamdeck/20261023.100/steamdeck-20261023.100-3.9.5.img.zst` (3.1 GiB, manifest variant `steamdeck`), in the recovery image's single-slot layout (esp 63, efi-A 128, rootfs-A 5120, var-A 256, home 2047 MiB; my parse). It's a possible way to start A at 3.9.5; whether it carries `/home/deck/tools/repair_device.sh` is *check*. **No checksums or signatures are published.** For the `-latest` alias, `.sha256`, `.sig` and `.asc` all redirect to the image; for dated names they are 404, and `SHA256SUMS` is a 404. So HTTPS is the only integrity check; S1 pins our own SHA-256 on first fetch | Ubuntu Core (stock image) |
| Disk | GPT, sfdisk's 1 MiB alignment, 512 B sectors (`repair:31-35, 67-77`): p1 `esp` 256 MiB, C12A7328-…; p2/p3 `efi-A`/`efi-B` 64 MiB each, **EBD0A0A2-B9E5-4433-87C0-68B6B72699C7** (Microsoft basic data, not ESP); p4/p5 `rootfs-A`/`rootfs-B` 5120 MiB each, 4F68BCE3-… (Linux root x86-64); p6/p7 `var-A`/`var-B` 256 MiB each, 4D21B016-… (Linux /var); p8 `home`, 933AC7E1-… (Linux /home), **a 100 MiB stub** (`PART_SIZE_HOME="100"`, "OS expands to fill physical disk on first boot", `repair:35`), grown at first boot. The system image's GPT is the same table, partition for partition, at 1 MiB alignment (my parse). PARTUUIDs are random per install; the system image's are fixed. Older (2022) installs have ESP 64 MiB, efi 32 MiB, roots 5 GiB each and home the rest of the disk (`randombk/steamos-teardown docs/partitions.md:74-89`, an fdisk listing). When this changed is *check*, so a restore copies the source's table and assumes no sizes. `sc:common.mk:42-45` also names `verity-A`/`verity-B` (10 labels); `repair` never makes them. GPT attribute bits are 0 on every partition of the recovery and system images (my parse; *check* on the installed guest) | Ubuntu Core (fixed roles, `holt/src/snapcore.rs:30-60`) |
| Filesystems | **The labels depend on how the disk was made.** Installer installs (path 1, R3): `esp` vfat label `esp`, both efi partitions vfat label **`efi`** (one label twice), FAT32 as in the recovery image (`fmt_fat32 efi`, `repair:266-268`), and both var partitions ext4 label **`var`**. The system image (path 2, the A100): `esp` FAT16 label `esp`, efi-A FAT16 label **`efi-A`** and efi-B **`efi-B`** (my parse of the boot sectors at LBA 526336 and 657408); its var labels are *check*. Every tool reads both forms, and the SticTest1 fixture and the corpus cover both. home `mkfs.ext4 -O casefold -T huge -L home`, then `tune2fs -m 0` (`repair:118-119, 255-276`). Both rootfs **btrfs label `rootfs`**: sectorsize 4096, nodesize 16384, incompat `compress_zstd`, `no_holes`, `skinny_metadata` and others, free-space-tree, no subvolumes; 3.3 GiB used of 5 GiB in 3.8.14 (my parse of the recovery image) | |
| Slot copies | The installer `dd`s its own rootfs into both slots, then runs `btrfstune -f -u` on each, so each slot gets its own fsid (`repair:186-194, 335-347`). An update writes the bundle's rootfs raw (`sc39:atomic-update/rauc/system.conf.in` `type=raw`), so the updated slot carries **the build's fixed fsid**. 3.8.14's, 651004c4-7509-4853-b71c-cacc991d8c8d, is in the recovery rootfs, in the system image's rootfs-A, and in the recovery `grub.cfg`'s `search --fs-uuid` (scratchpad `steamos/imghead/x/efi/steamos/grub.cfg:68`). The system image's rootfs-B fsid is *check* | |
| Mounts | `/` is self/rootfs, the btrfs top level, mounted **rw**; it is read-only only through the `ro` property (`sc:mkinitcpio/initcpio/hooks/holo.tmpl:116`). `/var` is self/var, mounted in the initrd (`sc:initrd/holo-var.ash`; it falls back to a tmpfs if the mount fails). `/etc` is an **overlay**: `lowerdir=/etc`, `upperdir=/var/lib/overlays/etc/upper`, `workdir=.../work`, `index=off`, and the **workdir is wiped every boot** (`sc:initrd/holo-etc-overlay.ash:22-46`). `/home` is a native unit with `Options=defaults,nofail,x-systemd.growfs` (`sc:misc/systemd/home.mount.in:19`), and repart grows its partition (`sc:misc/repart.d/90-home.conf`). `/efi` and `/esp` are vfat, `umask=0077`, and were seen as autofs automounts with a 60 s timeout (`randombk/steamos-teardown docs/partitions.md:120-121`). **The mechanism is *check* (S1):** sc puts `x-systemd.automount,x-systemd.idle-timeout=1min` (`sc:misc/systemd/efi.mount.in:19`, `esp.mount.in:19`) and `x-systemd.growfs` in native `.mount` units and ships no `.automount` unit, but systemd.mount(5) says `x-systemd.growfs` "can only be used in /etc/fstab, and will be ignored when part of the Options= setting in a unit file" (https://www.freedesktop.org/software/systemd/man/latest/systemd.mount.html), and automount units come from fstab-generator. So something else makes the automounts, and what grows home's filesystem is *check*. **Offload binds** from `/home/.steamos/offload/<path>` cover `/opt /root /srv /var/cache/pacman /var/lib/docker /var/lib/flatpak /var/lib/systemd/coredump /var/log /var/tmp` (and `/nix` if it exists) (`sc39:offload/systemd/*.mount.in`). `/etc/fstab` is essentially empty (`holo:filesystem/fstab`; the image's is *check*) | Vanilla (`/etc` overlay) |
| Partsets | `holo-partsets OUTDIR` writes `all`, `self`, `other`, `shared`, `A`, `B` (and `dev` if there is one), each a list of `<name> <partuuid>` lines, into `/efi/SteamOS/partsets/` on each efi partition (`sc:misc/bin/holo-partsets.in:269-356`). The ESP is found by its type GUID. GRUB adds `steamos.efi=PARTUUID=<its own efi partition>` to the command line. The initrd's `holo` hook mounts that partition, writes udev rules for `/dev/disk/by-partsets/<file>/<name>` for every partsets file (`all` and `dev` included), and sets `root=/dev/disk/by-partsets/self/rootfs` (`sc:mkinitcpio/initcpio/hooks/holo.tmpl:48-117`, `sc:initrd/holo-udev-rules.ash:31-91`). **So every PARTUUID must survive the restore**, or the partsets must be rewritten | none |
| Boot chain | UEFI → **`steamcl`** (gnu-efi, unsigned) at `esp:/efi/steamos/steamcl.efi`, plus the fallback `esp:/efi/boot/bootx64.efi`. NVRAM entry from `steamcl-install`: `efibootmgr -c -d DISK -p ESPNO -L "$PRETTY_NAME" -l '\EFI\steamos\steamcl.efi'` (`efi:util/steamcl-install.in:418-470`). steamcl reads `esp:/SteamOS/conf/{A,B}.conf` and walks them from newest `boot-requested-at` to oldest, `image-invalid` ones counted older than the rest, **skipping any with `boot-other` set** (`efi:chainloader/bootload.c:1219-1235`; the efi: README calls `boot-other` "DEPRECATED, no user tools set this by default", but `holo-reboot --reboot-other` does). At 3 or more failed boots (`boot-attempts`) of the chosen slot it shows its failsafe menu, which times out after 30 s and boots the default; at 6 or more it pre-selects the other slot, if that one isn't more broken, with a 120 s timeout (`bootload.c:42-46, 1157-1176, 1266-1270`). In 3.9.5's steamos-efi both stages increment `boot-attempts` unconditionally: stage 1 in `process_boot_config` (`bootload.c:879-897`, called at `:1431`; commit 091ac90, whose date is a squashed HEAD's), and stage 2 in GRUB's steamenv (`holo:grub/steamos-0001…patch` around +1650-1654). So one failed boot may count twice, giving the menu after 2 failed boots and the switch after 3 (*check*). Stage 2 also sets `image-invalid` past a built-in limit (efi: README, Stage 2). steamcl hands off to the slot's **GRUB**, `efi-X:/EFI/steamos/grubx64.efi` (patched with the `steamenv` module, `holo:grub/steamos-0001…patch`). Its embedded early config names **the slot's btrfs fsid** (`sc:grub/libexec/holo-grub-mkearlyconfig.in:67-141`). GRUB then reads `efi-X:/EFI/steamos/grub.cfg`: `search --fs-uuid --set=root <fsid>` in 3.8.14 (`search --part-uuid` once `GRUB_DISABLE_UUID=true` lands; whether 3.9.5 has it is *check*), then `steamenv_boot linux /boot/vmlinuz-linux-neptune-… console=tty1 rd.luks=0 rd.lvm=0 …` and `initrd /boot/amd-ucode.img /boot/initramfs-linux-neptune-….img`. Kernel and initramfs live inside the read-only rootfs. Secure Boot must be off (no shim in the manifest; Valve's FAQ says other hardware "may need to disable Secure Boot") | none |
| Boot records | `holo-bootconf` (`efi:bootconf/bootconf.c:158-230, 770-866`). Keys: `boot-requested-at`, `boot-other`, `boot-other-disabled`, `boot-attempts`, `boot-count`, `boot-time`, `image-invalid`, `update*`, `loader`, `partitions`, `title`, `comment`, `verbose`. Modes (`set_mode`, `bootconf.c:795-866`): `shutdown` and `reboot` clear this slot's `boot-other`; **`reboot-other` sets `boot-other=1`, the newest `boot-requested-at` and `image-invalid=0` on this slot's conf and clears `boot-other` on the others**; `first-boot` clears it too; **`booted` touches only `invalid`, `boot-attempts`, `boot-count` and `boot-time`**. So `boot-other` is sticky across good boots, and `booted` writes the key `invalid`, not `image-invalid`, so a later good boot doesn't clear an `image-invalid` that stage 2 set. `holo-boot.service` (`After=` and `Requisite=graphical.target`) runs `set-mode booted` (`sc:chainloader/systemd/holo-boot.service.in:22`). **A boot that never reaches graphical.target is never marked good** | none |
| Initramfs | mkinitcpio 41, `HOOKS=(base udev plymouth holo steam-deck modconf keyboard block filesystems resume)` (`sc:mkinitcpio/mkinitcpio.conf.d/20-steamdeck.conf`); no encrypt, lvm or systemd hook. Built at image build time, inside the read-only rootfs; nothing regenerates it at boot. Kernel modules live under `/var/lib/modules`, copied from `/usr/share/factory/var/lib/modules` only if missing (`sc:initrd/holo-var-lib-modules.ash`). So each var-X holds its own slot's modules | Arch |
| Read-only and dev mode | `holo-readonly disable` runs `mount -o remount,rw /; btrfs property set / ro false; date > /.ROOTFS_RW`. `holo-devmode enable` also re-inits the pacman keyring in the upper. The pacman DBPath is `/usr/lib/holo/pacmandb/` (`sc:misc/bin/holo-finalize-install.in:188-192`). Root changes are lost at the next update | MicroOS (read-only btrfs root) |
| Updates | The Steam client calls `atomupd-daemon` over D-Bus (`com.steampowered.Atomupd1`, D-Bus-activated `atomupd.service`). The daemon runs `steamos-atomupd-client`, which drives **RAUC** 1.15.2 with desync (`sc:atomic-update/rauc/system.conf.in`). The update writes the **other** slot's rootfs raw, in place, seeded from `/var/lib/steamos-atomupd/rootfs.caibx`, with `--regenerate-invalid-seeds`. post-install (`sc:atomic-update/rauc/post-install.sh.in`) **reformats the other var** and rsyncs the current `/var` into it with `holo-sync-var all`, keeping only the keep-listed `/etc` upper files (`sc:atomic-update/rauc/atomic-update-keep.conf.in`). It then runs `holo-finalize-install` in a chroot of the new slot (`steamcl-install`, `grub-mkimage`, `update-grub`), sets `first-boot` on the new slot's conf, and writes `/run/steamos-atomupd/reboot_for_update` and **`/run/steamos-atomupd/holo-booted-slot-sync-trigger`** (the booted slot's letter; `sc:common.mk:128-130`, post-install's last block). That trigger conditions **`holo-post-update-shutdown.service`** (`WantedBy=shutdown.target`), which at shutdown runs `holo-sync-var catchup` into the new slot's var and `holo-finalize-install --no-boot-install` in its chroot (`sc39:misc/systemd/holo-post-update-shutdown.service.in`, `misc/libexec/holo-post-update-shutdown.in`). So after an update the new slot's var changes once more at shutdown. No timer starts an update, and the daemon has no "disable" switch. Whether Game Mode downloads OS updates on its own is *check* | Vanilla's `abroot upgrade` |
| Encryption | **No block encryption.** The command line forces `rd.luks=0 rd.lvm=0 rd.md=0 rd.dm=0`, there's no encrypt hook, and crypttab is Arch's template. **Per-directory fscrypt** through `dirlock` 0.0.1.214 is in 3.9.5 (its manifest.pacman): a PAM module, and a keystore in `/var/lib/dirlock` on **var**, with password, TPM2 and FIDO2 protectors (`holo:dirlock/PKGBUILD:631-650`, which is 0.0.1.463, 3.10.1's version; that these parts are the same in 0.0.1.214 is *check*). Its GUI converter is in neither 3.9.5 nor 3.9.7. Whether stable users can turn it on is *check* | none |
| Users | `deck` (UID 1000) on Deck images, with no password. OOBE builds use a placeholder `doorstop`; `holo-finish-oobe-migration` moves `/home/doorstop` to UID 1000's home but doesn't rename the user (`sc:misc/libexec/holo-finish-oobe-migration.in`). Both install images are OOBE builds, so `doorstop` is the likelier name, but a new install's username stays *check*. Autologin is `User=deck`, `Session=gamescope-wayland.desktop` (Game Mode); `steamosctl set-default-login-mode desktop` makes Plasma the default | |
| Packages present (3.9.5) | openssh 10.4p1 (sshd **off** by default), avahi 0.9rc5 + nss-mdns (enabled by default is *check*), firewalld 2.5.0 + jupiter-firewall (its public zone allows ssh and TCP/UDP 1024-65535), python 3.14.6, rsync, btrfs-progs 7.1, compsize, e2fsprogs 1.47.4, dosfstools, gptfdisk, efibootmgr 18, util-linux, attr, acl, sudo, zstd; `amd-ucode-neptune` but **no intel-ucode**; kernel linux-neptune-72 7.2.7 (stable 3.9.5's manifest.pacman) | |

## What works today, and what breaks

**`bko-holt dump`:**

- `osrel.rs` `SUPPORTED` (`holt/src/osrel.rs:166`) has no `steamos` row, so dump refuses it. `ID_LIKE=arch` doesn't count.
- **Only the booted slot's rootfs is found as a mount.** `/` is a real btrfs device, so `storage.mounts` has it, and `/var` and `/home` too. The `/etc` overlay is dropped as a pseudo filesystem, which is right. `/efi` and `/esp` show up only if something touched them in the last minute. Otherwise they are autofs entries, which `mounts.rs` drops as pseudo filesystems (`autofs` in PSEUDO, `holt/src/mounts.rs:91`), so an untriggered `/efi` or `/esp` is simply absent from `storage.mounts`. The other slot is in nothing.
- **The offload binds** (`/var/log` and the rest) are binds of the home filesystem. *Check* that dump lists them as binds of `home` and not as filesystems of their own.
- **The read-only top level is lost.** `btrfs subvolume list -a -r` never lists subvolume 5 (`holt/src/report/btrfs.rs:82`), so `ro` on `/` isn't recorded.
- **btrfs compression isn't a mount option here.** The rootfs is zstd-compressed at build time. dump records compression per subvolume and per root inode (`holt/src/report/btrfs.rs:199`), and load restores btrfs compression through fstab mount options plus `btrfs property set ... compression` (test at `holt/src/load/commands.rs:11685`); mkfs.btrfs has no compression flag (`mkfs_btrfs`, `:3565`). SteamOS's top level has neither a property nor an fstab line (*check* the property), so the only signal dump has is the superblock's `compress_zstd` incompat flag (`holt/src/probe/btrfs.rs:30`). If the restore writes files uncompressed, 3.3 GiB of compressed data may not fit a 5 GiB partition (*check* with `compsize /` on the guest).
- **The other slot's btrfs facts aren't read.** `btrfs_json` reads `ro`, compression and allocation profiles only from btrfs filesystems in `ctx.mounts` (`holt/src/report/btrfs.rs:51-77, 197-207`), and rootfs-O is never there. ABRoot needed the `known` side channel for exactly this (`btrfs_json(.., known)`, `btrfs.rs:29-34`; `abroot::Dump::btrfs_profiles`, `holt/src/abroot.rs:303-306`; docs/design/vanillaos.md V7). Without it rootfs-O's `ro` isn't recorded, and load's mkfs.btrfs falls back to default `-d`/`-m` profiles: default DUP metadata on a 5 GiB filesystem with 3.3 GiB used is a real fit risk.
- **Packages:** pacman's database is at `/usr/lib/holo/pacmandb/`, not `/var/lib/pacman`. *Check* that dump's package list reads it.
- **Loader:** `detect_loader` sees an unknown `\EFI\steamos\steamcl.efi`, or GRUB on a Microsoft-basic-data partition. Neither leads anywhere useful.
- **Works as is:** kernels in `/boot` (`kernels_json` lists the booted rootfs's `/boot`), efibootmgr, the command line, ext4 features including `casefold` (`holt/src/probe/ext.rs:49`, `holt/src/load/commands.rs:3318`), and vfat labels and volume IDs (`holt/src/load/commands.rs:3658`).

**`bko-holt load`:**

- **The mount stage** mounts fstab's tree, but SteamOS has no fstab lines for `/`, `/var`, `/home`, `/efi` or `/esp`. So nothing is mounted, and nothing would restore.
- **The gate** needs `etc/fstab` (`holt/src/other/mod.rs:328-345`). *Check* that the rootfs has one.
- **The boot stage** would chroot and run mkinitcpio (writing into a read-only image), `grub-install` (onto the ESP) and `update-grub`. All of these are harmful.
- **Partitions:** `sfdisk_script` (`holt/src/load/commands.rs:795`) writes type, PARTUUID, name and attributes, which is right. `gpt_attrs` (`:773-788`) refuses bits 3-47; the recovery and system images set no bits at all (my parse; *check* on the installed guest).
- **NVRAM:** `efi_entries` (`:8034`) recreates the dumped entries, which is right.

**rustic-bko** (the fork):

- Overlay whiteouts (char 0:0) and `trusted.overlay.*` xattrs come back: Vanilla's SticTest1 showed both archivers keep them (docs/design/vanillaos.md, V6).
- **The ext4 casefold flag (`+F`) on directories in `/home`:** Slackware's SticTest1 has this need (`testvm/stictest1/slackware15/judge.py:73`). Its result there, and which home directories carry `+F`, is *check*. A directory restored without `+F` on a casefold filesystem behaves case-sensitively, and Proton prefixes may then miss files.
- **fscrypt directories** (dirlock): a file backup reads plaintext only while the directory is unlocked, and the restore loses the policy. Refused in v1 (below).

**Guest tools, BackShot1:**

- **vm-mount** finds no fstab tree.
- **vm-update** takes `has pacman` (`testvm/update/in-guest.sh:1342`) and would run pacman against a read-only root.
- **bs1-adopt**'s `pkg_install` would run `pacman -Sy` (`testvm/realworld/backshot1/bs1-adopt:185`) on a read-only root.
- **check_coverage** stops on the inactive slot.

## The design

### One tree convention: post-install's shape

Every tool that backs up or mounts a SteamOS disk builds the same tree.
S is the booted ("self") slot and O is the other one:

| Path in the tree | Filesystem | Found by |
|---|---|---|
| `/` | `rootfs-S`, btrfs top level | the device at `/` (running). load: the dump's `boot.steamos.self`, never the confs, since load restores the backup's `/` onto the partition it came from. vm-mount (a bare disk, no dump): the slot that the ESP's confs select, by steamcl's own rule (newest `boot-requested-at` first, `image-invalid` ones last, skipping any with `boot-other` set; or `holo-bootconf selected-image`'s answer, mirrored) |
| `/var` | `var-S`, ext4 | the mount at `/var` (running); partlabel `var-S` (offline) |
| `/efi` | `efi-S`, vfat | the automount `/efi`, triggered first; partlabel `efi-S` (offline) |
| `/esp` | ESP, vfat | the automount `/esp`, triggered first; partlabel `esp` (type C12A7328-…) |
| `/home` | `home`, ext4 | the mount at `/home`; partlabel `home` |
| `/mnt` | `rootfs-O`, btrfs top level | partlabel; never mounted on the source |
| `/mnt/var` | `var-O` | partlabel; never mounted |
| `/mnt/efi` | `efi-O` | partlabel; never mounted |

The other slot is a chroot of its own partset at `/mnt`, the shape that post-install uses (`mount -o ro` rootfs-O on `/mnt`, var-O on `/mnt/var`, `sc39:atomic-update/rauc/post-install.sh.in:219-220`) and that `holo-post-update-shutdown` uses too. (`holo-chroot --partset O` mounts the rootfs read-only in a `mktemp` directory instead, with the ESP at `X/esp` too; `sc39:misc/bin/holo-chroot.in:195-240`.) Its `/var` and `/efi` mount points already exist in its own rootfs. `/mnt` exists in rootfs-S, since post-install mounts there; that it's empty is *check*. The tree is read **raw**:

- `/etc` is the lower, and the upper is in `var/lib/overlays/etc/upper`.
- `/var/log` and the other offload paths are var's own empty mount-point directories. Their content is in `home/.steamos/offload/`.
- No bind is followed.

Restoring the eight raw filesystems restores exactly the composed view that the next boot assembles. Every filesystem keeps its UUID or fsid, its label, and its partition's PARTUUID, name and type. load already does all of this.

**Who builds it.** As for ostree and ABRoot (Bazzite's Decision 4, Vanilla's Decision 4), the work is split:

- **On a running source**, `dump` writes `storage.backup_sources` with all eight entries.
- **The three unmounted entries** have `"from": null` and a `mount` hint:
  - rootfs-O: `{"device": "/dev/disk/by-partlabel/rootfs-O", "fstype": "btrfs", "options": "ro,rescue=nologreplay,nodev,nosuid,noexec"}`.
  - var-O: `ro,noload,...`.
  - efi-O: `ro,...`.
  - These are `abroot::ro_options`' rules (`holt/src/abroot.rs:42-49`).
- **`/efi` and `/esp`:** dump triggers both automounts (by a `stat` of `SteamOS/` in each) before it reads them. Their entries name the running mount and carry a `note` saying a backup must hold them mounted. A read-only bind of each into the tree keeps the automount busy, so the idle timeout can't unmount it mid-backup (*check*).
- **The `/` entry's note** says to read it through a non-recursive bind, as on ABRoot.
- **On a disk**, load and vm-mount derive the same tree from the layout and the partlabels: load from the dump's `self` and `other`, vm-mount from the confs.
- **A corpus test ties the two:** for every SteamOS corpus dump, `backup_sources`' paths equal the mount points load plans, and each path's device (partlabel and PARTUUID) equals the one load mounts there.

### Detection

SteamOS is recognised by its partition roles, not by os-release alone. The disk holding `/` must have the eight partlabels `esp`, `efi-A`, `efi-B`, `rootfs-A`, `rootfs-B`, `var-A`, `var-B` and `home`, and `/` must be `rootfs-A` or `rootfs-B`. os-release's `ID` must also be `steamos`. Then `boot.steamos` is written. A disk with the roles but missing pieces (a `verity-X` partition, no `efi-B`, the recovery stick's single slot) still gets `boot.steamos`, and load refuses it. `boot.steamos` is absent on every other system, so nothing else changes.

### `dump`

**`boot.steamos`**, a new section (`holt/src/steamos.rs`, after `snapcore.rs`'s ROLES and `abroot.rs`'s read-only mounts). It records:

- `self` and `other` (A or B).
- `roles`: per partlabel, the partition's device, PARTUUID, type GUID, fstype, UUID, label and `mounted_at`.
- `esp`:
  - `steamcl_version`: the first line of `steamcl-version`.
  - The flag files (`steamcl-restricted`, `-verbose`, `-menu`, `-nvram-debug`).
  - `factory_reset`: whether `efi/steamos/factory-reset/` holds any `*.cfg`.
  - `confs`: every key of `SteamOS/conf/{A,B}.conf`.
  - `selected`: the slot steamcl would boot, by its rule (newest first, invalid last, `boot-other` skipped), with the `boot-other` flags as read.
- `slots`: per slot,
  - From its efi partition: `grub_cfg` (the `search` line's kind and value, and the `linux` and `initrd` paths), `grubx64_efi` (sha256 and the early config's embedded fsid, *check* how to read it from the memdisk), `partsets` (each file's lines), and `roothash` (present or not).
  - From its rootfs: `manifest` (the fields of `etc/steamos-atomupd/manifest.json`), `ro` (`btrfs property get -ts ROOT ro`), `rootfs_rw_marker` (`/.ROOTFS_RW` exists), `kernels` (`boot/vmlinuz-*`, `boot/initramfs-*`), and the compression in use (`compsize`'s totals, if it's there).
  - From its var: `modules` (the `var/lib/modules/*` names) and `dirlock` (whether `var/lib/dirlock/policies` has entries).
- `pending`: whether `/run/steamos-atomupd/reboot_for_update` or `/run/steamos-atomupd/holo-booted-slot-sync-trigger` exists (the second means var-O will change again at shutdown), and whether `rauc.service` or `atomupd.service` is active.
- `mounts_skipped`.

The other slot is read through dump's own read-only mounts at `/run/bko-holt/steamos-PARTLABEL` (by partlabel: the filesystem labels repeat, `efi`, `var` and `rootfs` twice each on an installer install), made through `Sys`, then unmounted and removed, as on ABRoot (`holt/src/abroot.rs:317-345`). While rootfs-O is mounted, dump reads its `ro` property and its allocation profiles, and hands them to `btrfs_json` through the `known` side channel, as `abroot::Dump::btrfs_profiles` does (`holt/src/abroot.rs:303-306`, `holt/src/report/btrfs.rs:29-34`). While an update is running (rauc active), dump mounts nothing of its own, warns, and records `mounts_skipped: true`. load refuses such a dump.

**Other changes in dump:**

- **`storage.backup_sources`:** the eight entries above.
- **`kernels_json`:** also `boot/vmlinuz-*` of the other slot, marked with its slot (`dir` `/mnt/boot`, `slot`).
- **Generic, any system: the top level's `ro` property** (S3 below). `report/btrfs.rs` records subvolume 5's `readonly`, from `btrfs property get -ts MOUNT ro` when MOUNT is the top level, alongside the listed subvolumes. load then restores it like the others.
- **`redact.rs`:** `efi`, `esp`, `rootfs`, `home` and `var` are already in GENERIC_LABELS (`holt/src/redact.rs:242` ff.). The list is lower case, so `efi-a`, `efi-b`, `rootfs-a`, `rootfs-b`, `var-a` and `var-b` are added, and the `[&str; 88]` length goes up. `steamdeck` goes into GENERIC_HOSTS (`:185`; the default hostname is *check*). Labels don't affect PARTUUID redaction; the partsets' PARTUUIDs are redacted as any PARTUUID is.
- **The `SUPPORTED` row:** `("steamos", "3", "SteamOS 3")`, with a test beside `vanilla`'s (`holt/src/osrel.rs:965-969`).

### The read-only top level and compression (generic)

1. **dump** records the FS_TREE's `ro` as above.
2. **load** sets it back in `boot_tail` (`holt/src/load/commands.rs:6921-6972`), after the atimes, with `btrfs property set -ts {t}/PATH ro true` on the mounted top level. That has to come last, because nothing can be written to the filesystem after it.
3. **Compression.** load writes a rootfs through a mount with `compress-force=zstd` for the restore, so the data fits the source's 5 GiB again. The only signal is the superblock's `compress_zstd` flag (`holt/src/probe/btrfs.rs:30`), and 10 of the corpus `src.json` files have it, so a generic "the source used zstd" rule would move their golden plans. **It is scoped:** only a top level that has `compress_zstd`, no compression property and no fstab `compress` option, which in practice is SteamOS (or simply `boot.steamos`), gets it. *Check* in S1 that the uncompressed size exceeds the partition. `compress-force` and not `compress`, since Valve's build may have forced it (*check*). It's an option of the restore mount only: SteamOS mounts `/` itself, with options of its own.
4. **Plan problem:** if a rootfs's uncompressed size, from the dump, can't fit even at the source's compression ratio, the plan says so before partitioning.

### `load`

- **Mount stage:** the eight roles from `boot.steamos`, mounted at the tree's paths in depth order, S and O taken from the dump's `self` and `other` (never from the confs: when an update finished but wasn't rebooted into, the confs select O while the backup's `/` came from S). `mnt` is created with `mkdir -p` only if the restored rootfs-S lacks it (it shouldn't). All mounts are rw, and the rootfs ones are `compress-force=zstd`. This hooks in beside `abroot_mounts` and `snapcore_mounts` (`holt/src/load/commands.rs:5438-5446`).
- **Gate and status:** `restored_marker()` (`holt/src/other/mod.rs:328`) and `holt/src/load/status.rs:376-385` take `etc/steamos-atomupd/manifest.json` on the restored rootfs-S.
- **The boot stage:** an early `if self.steamos { return self.steamos_boot(); }` in `boot_stage` (`holt/src/load/commands.rs:6079`), beside ostree's and abroot's, in their order:
  1. The atimes listed `if-missing` first.
  2. A note, worded as abroot's (`holt/src/load/abroot.rs:326`): the ESP, both efi partitions and both rootfs are restored as they were. No mkinitcpio, no `grub-install`, no `update-grub`, no `steamcl-install`, no chroot. Every PARTUUID and fsid that the partsets, grub.cfg and grubx64.efi name is kept.
  3. **A consistency check**, as a shell test the plan shows:
     - The GPT's PARTUUIDs equal every partsets file on both efi partitions, line for line. Each slot's `self` names its own `rootfs`, `efi` and `var`, and `other` names the other slot's.
     - Each slot's `grub.cfg` `search` names that slot's own fsid (or PARTUUID), and its `linux` and `initrd` files exist in that slot's `/boot`.
     - `steamcl.efi` and `bootx64.efi` exist on the ESP.
     - `SteamOS/conf/A.conf` and `B.conf` exist, and the slot they select (steamcl's rule, `boot-other` included) is the dump's `selected`. When that isn't the dump's `self` (an update finished, not yet rebooted into), the plan has a note: the restored system boots the new slot first, as the source would have.
     - No factory-reset `*.cfg` is on the ESP.
     - The two rootfs fsids differ.
     - Any mismatch fails the stage.
  4. `efibootmgr` entries as today (`holt/src/load/commands.rs:8034`), unless `--no-nvram`. The dumped entry is steamcl's `\EFI\steamos\steamcl.efi`. A source whose NVRAM never had one (it boots through `bootx64.efi`) gets none: faithful, and the firmware's removable-media path still boots it.
  5. **The log** goes into `{t}/home/.steamos/offload/var/log/bko-holt`, where the running system's `/var/log` really is. Writing into var's own `var/log` would hide the log under the bind.
  6. The generic tail: atimes put back, then `ro true` on both rootfs top levels (S3), whatever the dump recorded. A source with `ro false` (dev mode) is restored as it was, with a note.
- **Plan problems on SteamOS, v1:**
  - A `verity-A`/`verity-B` partition or a `roothash` (dm-verity).
  - A pending factory reset.
  - A `dirlock` policy in either var.
  - Equal fsids on the two rootfs.
  - A missing role.
  - A running update (`mounts_skipped`).
  - BIOS.
  - `--new-uuids`, `--fit-largest`, and `--new-machine`'s partition changes. The partsets name PARTUUIDs, so new ones would need `holo-partsets` run in a chroot. That's not v1 (Decision 4).
  - A target disk smaller than the end of the source's `var-B` (home may shrink, as `--fit-largest` would do elsewhere: *check* whether that's wanted; v1 refuses).

### What the restore leaves to SteamOS at first boot

All of these are SteamOS's own normal first-boot or every-boot work, and the restore doesn't do them:

- The `/etc` workdir is wiped and remade by the initrd.
- `holo-boot.service` marks the boot good.
- systemd-repart grows home's partition if the target disk is bigger (*check* that repart runs on every boot), and something grows its filesystem (*check*: `x-systemd.growfs` in a native unit is ignored, the Mounts row).
- `swapfile.service` remakes `/home/swapfile` if the restored one is holey (Decision 11).
- `holo-install-grub` and `holo-install-steamcl` run only if their files are missing, and they aren't.

## Both slots bootable after a restore

- **Setting it up.** The guest and the A100 both start as 3.8.14 in both slots. A then has the install image's build and B its `btrfstune` copy, or the system image's fixed one (*check*). One update, `steamos-update check` and then `steamos-update` (atomupd; *check* that it runs from desktop mode without a Steam login), writes B raw with 3.9.x, so B carries that build's fixed fsid, gets its own GRUB, and becomes the default. A stays 3.8.14 as the previous slot (variant `steamdeck-oobe`; B's is `steamdeck`).
- **Markers.** Before the update, the maker writes `/etc/bko-slot` with `a` into var-A's upper. After the reboot into B, it writes `b` into var-B's upper. The file isn't on the keep list, and it must stay off bko.conf too (the Guests section): a keep entry would make `holo-sync-var all` copy the old slot's letter into the new slot's upper on every later update (`sc39:misc/libexec/holo-sync-var.in` `rsync_etc_overlay`; the keep list's line `@atomic_update_conf_d@/*.conf`).
- **If the update can't run headless:** both slots keep 3.8.14 with distinct fsids (the installer's `btrfstune -u`), the markers still tell them apart, and the test is weaker but valid.
- **Testing it.** After the restore, boot the restored disk alone:
  1. **Default boot:** over SSH, `holo-bootconf this-image` must be the source's selected slot (B). The slot's `manifest.json` buildid must be the source's, `/etc/bko-slot` must read `b`, and `/proc/cmdline` must equal the source's.
  2. **The other slot:** `sudo holo-reboot --reboot-other` (`sc:chainloader/bin/holo-reboot.in`). This is SteamOS's own tool. It sets `boot-other=1` and the newest `boot-requested-at` on B's conf, on the ESP only, a file every boot already rewrites. Then the same checks for A (3.8.14, `a`). **The flag is sticky:** `booted` never clears it (`efi:bootconf/bootconf.c`, `set_mode`), so the machine keeps booting A on every reboot until step 3.
  3. **Back:** `holo-reboot --reboot-other` again, from A, then B's checks. B is chosen again only because reboot-other from A clears B's flag and sets A's. The confs then end with A.conf `boot-other=1` and the newest stamp: that is SteamOS's own normal state after a round trip, and the EXPECTED rule allows it.
- **The fallback** (Decision 7) is the steamcl menu: it shows when F3 or "…" is held, or when the `steamcl-menu` flag file is present (`efi:` README). It's driven by `console-keys.py` in QEMU, and by a person at the A100.
- **Unattended boots must reach graphical.target**, or `boot-attempts` grows each boot. At 3 failed boots steamcl shows its failsafe menu for 30 s and then boots the default; at 6 it pre-selects the other slot (120 s). If both stages count one failed boot (3.9.5's steamos-efi), that is after 2 and 3 failed boots (*check*). Either way every later boot is slowed and the slot may switch unasked. So the guest keeps a display manager and a desktop autologin (Decision 12). Every HoltTest1 and BackShot1 boot checks that `boot-attempts` is 0 for the booted slot after `holo-boot.service` (*check* the unit's name in 3.8.14, where scripts are `steamos-*`).

## Updates, and holding them during a run

- **What can start an update.** Only the Steam client, through atomupd's D-Bus API. No timer does. RAUC's `pre-install.sh` stops all active timers for the update (`sc:atomic-update/rauc/pre-install.sh.in`).
- **Holding them, BackShot1:**
  - `systemctl mask --runtime atomupd.service rauc.service` for the run. A runtime mask lives in `/run`, so nothing is written to the `/etc` upper, and it blocks D-Bus activation too (*check*).
  - bs1-common's `UPDATERS` (`testvm/realworld/backshot1/bs1-common:180`) gains `atomupd rauc`, so that bs1-backup refuses while either is active (`bs1-backup:115-118`, once its gate takes SteamOS) and bs1-verify's `updaters_quiet` (`bs1-verify:115-129`) checks that neither ran since boot.
  - bs1-backup's updater loop runs only `if is_ostree || is_abroot` (`bs1-backup:115`), so adding UPDATERS alone refuses nothing: that gate becomes `|| is_steamos` too (the BackShot1 gates, below).
  - The preflight refuses `/run/steamos-atomupd/reboot_for_update` and `/run/steamos-atomupd/holo-booted-slot-sync-trigger`: an update was written and waits for its reboot, and the shutdown would still change var-O. A backup then would hold the new slot already selected and the old one running. dump allows it (the note above), but BackShot1 wants the slot it can check.
  - The user's step, documented: no Steam login in Game Mode during the run, or Steam's update setting off (*check* whether one exists).
- **Guests:** vm-update's `Linux.steamos` section (S5) is the only thing that updates them. HoltTest1 backs up offline, so nothing can update under it.
- **After a restore:** the system updates as the source did. `rootfs.caibx`, the desync seed, describes the source's raw slot bytes. The restored slot has the same files at different block offsets, so the seed is "invalid" and desync regenerates it (`--regenerate-invalid-seeds`, `system.conf.in`). The next update downloads more chunks but works (*check* once in S5 with a real update of a restored guest).

## Per-boot churn and bs1-manifest's rules

A SteamOS block in bs1-manifest, gated by the marker `STEAMOS_SOURCE = "usr/lib/steamos-atomupd/client.conf"` in the old manifest (`sc39:atomic-update/Makefile` installs it there, symlinked from `/etc`; `sc:common.mk:87-116`; verify on the guest), as `ABROOT_SOURCE` is (`testvm/realworld/backshot1/bs1-manifest:410`).

**STEAMOS_EXPECTED** (content may change):

- `esp/SteamOS/conf/A.conf` and `B.conf`: `boot-attempts`, `boot-count`, `boot-time`, `comment`, and on the other-slot boots `boot-other`, `boot-requested-at` and `image-invalid` (reboot-other writes `image-invalid=0`). The rule parses the file and allows only those keys to change.
- `var/lib/overlays/etc/work/**` and `mnt/var/lib/overlays/etc/work/**`: wiped every boot. The second changes only on the other-slot boot.
- `home/.steamos/offload/var/log/journal/**`: the journal is persistent, 50M (`sc:misc/journald.conf.d`).
- `home/swapfile`, `home/hibernation.swapfile`, `home/.hibernation.metadata` (hibernation is disabled on Deck in 3.9.4+, by `jupiter-hibernate-disable`).
- `var/lib/systemd/{random-seed,timesync/clock,timers/*}`, `var/lib/NetworkManager/*`, `var/lib/bluetooth/**`, `var/lib/sddm/**`, `var/lib/iwd/**`, and the same under `mnt/var/` for the other slot.
- `var/lib/steamos-atomupd/**` and `var/lib/overlays/etc/upper/steamos-atomupd/remote-info.conf` (written by update checks).
- The Steam client's churn under `home/<user>/.local/share/Steam/` (`logs/**`, `appcache/**`, `config/*.vdf`, `shadercache/**`) and `home/<user>/.steam/**`. Listed in S10 from the A100's first run, as Bazzite's were.
- `var/lib/overlays/etc/upper/machine-id` must **not** change. It's on the keep list and is the system's identity.

**STEAMOS_KEPT** (byte for byte):

- Everything on rootfs-S and rootfs-O, i.e. every path outside `var/`, `home/`, `efi/`, `esp/`, `mnt/var/` and `mnt/efi/`. This is the strongest single test: an image that nothing should write.
- `efi/EFI/steamos/{grubx64.efi,grub.cfg}` and `mnt/efi/EFI/steamos/...`.
- `efi/SteamOS/partsets/*` and `mnt/efi/SteamOS/partsets/*`.
- `esp/efi/steamos/steamcl.efi`, `esp/efi/boot/bootx64.efi`, `steamcl-version`, and the flag files.
- **STEAMOS_ADDED:** `home/.steamos/offload/var/log/bko-holt/**`.

**SteamOS's own conditional units**, each a *check* that it did nothing on the restored system:

- `holo-install-grub` and `holo-install-steamcl`, by the KEPT rules.
- `holo-fstab-repair` (rewrites the upper fstab only if it has bad `/dev/mmcblk*` lines).
- `ConditionNeedsUpdate=/etc` units: the restore keeps `/etc/.updated` and its whiteout as they were.
- `jupiter-biosupdate --auto` and `jupiter-controller-update`: in jupiter-hw-support master (2026-11-24) both exit 0 on non-Valve DMI ("Skipping update due to unsupported hardware revision", `usr/bin/jupiter-biosupdate:145-150, 212-215`; `jupiter-controller-update:8-13, 45-48`). The copies in the restored images (3.8.14 and 3.9.x) are what runs: *check* on each.

## Guests

**`steamos3`**, UEFI with Secure Boot off (non-SB OVMF, as cchulo's PR #11), q35, `-cpu host`.

- **Memory:** 8192 MiB for builds and updates (Igalia's recipe runs with `-m 8G` and warns that "The OS update might fail if you use less"). Ordinary boots *check* at 4096.
- **Display and network:** `-device VGA`, so sddm reaches graphical.target (*check* that the desktop session starts on it), and virtio-net.
- **Disk:** a 64G sparse qcow2 on `-device nvme` (`repair` hardcodes `/dev/nvme0n1`, `repair:17-18`).
- **Inputs:** the recovery image in `/bkoscratch/rebuild-inputs/steamos3/`, with its SHA-256 pinned in the maker on first fetch, since there's no published sum. The third-party digest for the `.zip` (`d54fbf0a…a494`, cchulo `vm/README.md`) is a cross-check only, and only for the `.zip`.

How it's built (Decision 2):

1. **Valve's installer, unattended** (recommended). A new `testvm/make-steamos.sh FLAVOR IMG WORK_DIR`:
   1. Decompresses the recovery image into the work directory.
   2. Writes nothing into the recovery image. The work is handed to it as a systemd unit in an SMBIOS credential, as make-vanilla.sh does (`systemd.extra-unit.bko-install.service`, `testvm/make-vanilla.sh:37-38, 214-229, 278`). The unit runs `NOPROMPT=1 POWEROFF=1 /home/deck/tools/repair_device.sh all`, with its output to `/dev/ttyS0`. `NOPROMPT` skips every zenity step, and `POWEROFF` ends in a power-off (`repair:129-155`). make-vanilla.sh also passes `systemd.wants=` on a directly booted kernel's command line; here the kernel is booted by steamcl and GRUB, so the unit is pulled in by a second credential instead, a `systemd.unit-dropin.multi-user.target` drop-in with `Wants=bko-install.service`. 3.8.14 has systemd 257.7 (its manifest.pacman), new enough for both credentials, but that its systemd reads them is *check* in S1. The fallback is the offline unit in the copy's var-A upper.
   3. Boots it under QEMU: the copy as a virtio disk, the target as NVMe.
   4. Waits for the power-off or a timeout. `trap err ERR` makes any failure end in `sleep infinity` (`repair`), so the timeout matters.
   - `sanitize_all` falls back to `nvme format`, since QEMU's NVMe has Format NVM but no sanitize log (qemu `hw/nvme/ctrl.c`). That's *check* on 3.8.14. `jupiter-biosupdate` and `jupiter-controller-update` exit 0 off Valve hardware in jupiter-hw-support master (the churn section), but the install runs the 3.8.14 image's own copies under `set -e` and the ERR trap, so that they exit 0 there is *check*.
2. **The system image written onto the qcow2** (as `make-ubuntu-core.sh` does a stock image), then `sgdisk -e`. This is exactly the A100's path (Decision 9), rehearsed. It's simpler (est. -0.25 d). The layout is the same as the installer's (the same table, home a 100 MiB stub in both); the differences are that every guest from it has the image's fixed PARTUUIDs and fsids, and that its efi labels are `efi-A`/`efi-B` rather than `efi` (the Filesystems row).
3. **goldboot's VNC keystrokes** (`cilki/goldboot` `goldboot/src/builder/os/steam_deck/mod.rs:65-78`), the fallback if the unit can't run without a session.

After the install, the maker writes into the target's var-A upper, offline, with no SteamOS code run:

- `sshd.service` enabled: a symlink in `etc/systemd/system/multi-user.target.wants/`, which is keep-listed.
- Root's SSH key in `home/.steamos/offload/root/.ssh/authorized_keys` (`/root` is an offload bind).
- The fixed test host key in `etc/ssh/` (keep-listed).
- A password hash for `deck` (`etc/shadow` in the upper, keep-listed; *check* the username).
- A `tester` user.
- An sddm autologin drop-in, `etc/sddm.conf.d/zz-holo-autologin.conf`, with `Session=plasma` (Decision 12; keep-listed; the 3.8 name is `zz-steamos-autologin.conf`, `sc:misc/bin/holo-finalize-install.in` `migrate_session_config`).
- `etc/atomic-update.conf.d/bko.conf`, so that `etc/ssh/sshd_config.d/*` survives updates (the keep list's own extension point, `sc:atomic-update/rauc/example-additional-keep-list.conf.in`). Not `etc/bko-slot`: each slot keeps its own letter (Markers, above).
- The OOBE: both install images are OOBE builds, which may download and apply an OS update during the first boot, before any login (*check*). That would make the A→B update happen uncontrolled. The sddm drop-in skipping the OOBE is meant to prevent it on the guest; S1 checks that B is still 3.8.14 (or empty) before the maker's own update.
- A serial console: `console=ttyS0,115200` in both slots' `grub.cfg`. **This is the one regenerated file the maker edits, and every update rewrites it.** So vm-update re-edits the new slot's `grub.cfg` after each update, and HoltTest1's judge reads the source's, not upstream's. Whether a keep-listed `/etc/default/grub` drop-in can carry it instead is *check* (`/etc/default/grub` isn't on the keep list).
- Then the update into B and the two `/etc/bko-slot` marks (above), the reboot into B, power-off, and the `fresh-install` snapshot.

**Room** (est., *check* in S1):

- Two rootfs at ~3.3 GiB compressed each (~6-7 GiB of files each, uncompressed, in the archive; rustic dedups between the slots only where files are equal).
- Var ~0.1 GiB each, home a few hundred MiB without Steam's client bootstrap, and the swapfile 1 GiB.
- That gives a qcow2 of ~9-11 GiB, and HoltTest1's D ~22-28 GiB. It probably doesn't fit beside other runs in a 30G RAM disk. S7 measures it and sets `ram-sizes.tsv`. If it doesn't fit, it runs from `/bkoscratch` as vanilla3 does (`testvm/holttest1d/ram-sizes.tsv:107`).

**The other things a new guest gets:**

- A `rebuild-one.sh` line beside vanilla3's (`testvm/rebuild-one.sh:180-184`).
- Rebuild inputs on Chopin and Cinqcent.
- A GuestTest1 grade.
- The vm-store: Chopin publishes to Bigfoot and the farm fetches ([[image-store-bigfoot]]). Valve's EULA (the download page, Sources): §1.1 grants a limited, non-redistributable licence to install and use the "Steam Client Back-Up Image" on your computers "solely for your personal use"; §1.2(a) forbids copying, reproducing or modifying it, or creating derivative works from it. The guest is a modified copy (the offline seed files) that is then stored and copied between the user's hosts. Whether that's within the licence is the user's call: *check* with the user (Decision 13).
- Hosts: any farm host. No host-side tool is needed that the farm lacks: mounting the casefold home needs `CONFIG_UNICODE` on the host, which trixie-backports' 7.2 has (*check*).

**Guest tools:**

- **vm-mount, SteamOS mode** (beside `mount_abroot` and `mount_snapcore`, `testvm/vm-mount:792, 1029`). When the disk has the eight partlabels, it picks S by the ESP confs (steamcl's rule, `boot-other` skipped: the tree table) and mounts the eight-path tree at MP. It finds filesystems by partlabel only, since the labels repeat on installer installs and differ on system-image ones.
- **vm-chroot** chroots into `/` with `/etc` as the overlay (lower `etc`, upper `var/lib/overlays/etc/upper`), and with the offload binds, as at `testvm/vm-chroot:86-90`.
- **vm-update, `Linux.steamos`**, before `Linux.common` and the pacman branch, as `Linux.abroot` is (`testvm/update/in-guest.sh:397-470`):
  - `steamos-update check`, then `steamos-update` (atomupd; *check* the 3.9 name, `holo-update`, whose source isn't in the mirrors).
  - "No update available" counts as success.
  - A reboot into the new slot, then re-edit the serial console in the new slot's `grub.cfg`, then `qcow2-punch.py`.
  - Each update flips the slots, as on Vanilla. HoltTest1 asks the guest which slot is booted.
- **`update/pkgset.sh`:** `base pm steamos`, using `pacman -Q --dbpath /usr/lib/holo/pacmandb` on the booted slot, plus `base build BUILDID`.
- **`update/fingerprint.py`:** a `steamos` kind in `LINUX_KINDS` (`:99`), with the two builds in its boot section.
- **`compare/compare.py`:** reads the eight-path tree.
- **GuestTest2:** a `K-steamos` rule in `testvm/test2/t2lib/guest_judge.py` (beside K-abroot, `:35`), plus `check-image.sh` and `check-after.sh`: one kernel per slot, in each rootfs's `/boot`, and the booted slot equals the confs' selection.

## Tests

**SticTest1** (`testvm/stictest1/steamos3/`, after `vanilla3/`): `inner.sh`, `fixture.sh` and `judge.py`. The scratch disk is laid out like the guest:

- **Partitions:** eight GPT partitions with SteamOS's names and types, including Microsoft basic data for the efi partitions.
- **rootfs:** two btrfs filesystems labelled `rootfs`, made with zstd and files written through `compress-force=zstd`, and top-level `ro` set.
- **vfat:** two filesystems labelled `efi` (the installer's form), and a second run's layout with `efi-A`/`efi-B` (the system image's form).
- **var:** two ext4 filesystems labelled `var`, with an `/etc` upper holding a whiteout, an opaque directory and `trusted.overlay.*` xattrs.
- **home:** an ext4 `-O casefold` home with `+F` directories and a mixed-case tree.

Checks (archiver properties only, per `testvm/stictest1/README.md:1-8`):

- Each filesystem's files, with its labels and UUIDs.
- The `+F` flags.
- The whiteout and xattrs.
- Measured only, not judged: the restored top-level `ro` and the compressed sizes. Their real tests are S3's and S4's.

It follows `stictest1/vanilla3/` in shape, with a `check.sh`, and reuses its `gpt.py` and `fatattr.py`. It runs both archivers on two hosts. What restic does with `+F` and `trusted.*` is recorded, not fixed ([[cross-tool-compat-courtesy]]). It is registered in `testvm/test2/stic/manifests.json` and `limits.json`, and `check-manifests.py`'s count goes up by one.

**HoltTest1** (Decision 10: **1Obscure**): ht1.sh backs up the guest offline through vm-mount's tree, loads it onto a blank disk of the same size, and boots the restored disk alone three times: the default slot, then the other slot by `holo-reboot --reboot-other`, then back.

This doesn't fit ht1.sh as written. ht1.sh boots each root with `vm_down`/`vm_up`, the restored disk as a fresh `--snapshot` boot, the root picked on the serial menu by `GRUB_PICK` (`testvm/holttest1d/ht1.sh:509-545, 1333-1395`). `holo-reboot --reboot-other` needs an in-guest reboot inside one QEMU process: `steamos_verify` runs it over SSH, waits for SSH to drop and come back, checks, and repeats for "back", all in one `vm_up` whose writes persist until the end (a fresh `--snapshot` boot would throw away `boot-other`). The source does the same: `steamos_source`, after `abroot_source`'s shape (`ht1.sh:1333-1356`), boots the source, records the default slot's values, reboots it in place to the other slot, records those, and reboots back, so each slot has a baseline. It is dispatched beside abroot's in the source `case` (`ht1.sh:1830`) and in the verify list (`:2354`). Each boot must report over SSH:

- `holo-bootconf this-image` (or `steamos-bootconf`, on 3.8).
- The slot's `manifest.json` buildid and variant (`steamdeck-oobe` on A, `steamdeck` on B).
- `/etc/bko-slot`.
- `/proc/cmdline`.
- `findmnt -no PARTUUID /`.
- `btrfs property get / ro` = `ro=true`.
- `boot-attempts=0` for the booted slot.

Each must equal the source's value for that slot. judge.py **requires** STEAMOS_KEPT (both rootfs trees, both efi partitions' GRUB files and partsets, the ESP's steamcl files) to be byte-identical to the source's: that is the test that nothing was regenerated. It allows STEAMOS_EXPECTED. The rest of the wiring:

- ht1.sh: `steamos_source` and `steamos_verify`, after `abroot_source` and `abroot_verify` (`testvm/holttest1d/ht1.sh:1333, 1364`).
- judge.py: `STEAMOS_SOURCE` and `STEAMOS_KEPT`, after ABROOT's (`testvm/holttest1d/judge.py:343-344`), and a `STEAMOS_REQUIRED` beside `ABROOT_REQUIRED` (`:345`) with its call sites (`:501-504, 1055, 1110, 1183`), with tests in `judge-test.py`.
- A `ram-sizes.tsv` row.
- The 1Obscure row in `testvm/holttest1d/README.md:42`, and `testvm/test2/batches/holttest1all.batch:13`.

**Test2:**

- **Golden plan:** `holt/tests/corpus/steamos3/{src.json,plan.txt}` from HoltTest1's source dump, with its `index.tsv` row and the corpus README's provenance. The plan shows the eight roles' `sfdisk` lines with their names and types, no `mkinitcpio`, `grub-install` or `update-grub`, the check, `efibootmgr`, and the two `ro true` lines.
- **Registration:** `classes.json` (`holt.families.Obscure`, `testvm/test2/classes.json:46`, and the `uefi` list), `testvm/test2/holt/spec.json` (with its per-guest `harness` list, as vanilla3's at `:149`), `guest/spec.json` (pm `steamos`, S-none, L-none, K-steamos, firmware uefi, memory per S1), `testvm/test2/guest/limits.json` and `known-lines.json` (vanilla3's entries are at `limits.json:82` and `known-lines.json:121`; docs/design/vanillaos.md V8), `estimates.tsv`, and the counts in `t2lib/plan.py` and its README and tests.
- **Corpus:** the golden plan covers both label forms (installer `efi`/`var`, system image `efi-A`/`efi-B`), by a second scenario if the S10 dump isn't in the corpus.
- **Fixtures:** `tests/fixtures/p2/world.py`'s `GUESTS`, then re-bless the p2 goldens.
- **Scenario:** `steamos_tree` in `holt/src/scenarios.rs`, with a fresh-install variant and an after-update variant, in `dumps()`.
- **Done when** two corpus builds have the same sha256, leak scans are 0 on two hosts and the Mac, no other plan changes, and Test2's own tests pass.

**BackShot2:** a `families/steamos.py` beside `abroot.py` is out of scope for v1 (below).

## BackShot1 on the A100

### Feasibility

The A100 is a Lenovo IdeaCentre A100 all-in-one (F0J6006UFR): Intel N100 (Alder Lake-N, 4 E-cores), 16 GB, Alder Lake-N UHD graphics, RTL8168H wired, a Samsung PM9C1a 512 GB NVMe, Secure Boot off since 2026-11-17, and firmware that prefers the NVMe, so BootNext is used (docs/design/freebsd-bs1.md:76-86; testvm/realworld/backshot1/README.md:6-12).

| Need | SteamOS 3.9 | Status |
|---|---|---|
| Official support | Valve lists Deck, Machine, Frame and Legion Go S, with beta support for "other AMD powered handhelds" and AMD GPUs. **Intel isn't listed** (Valve's FAQ). 3.8 notes "improved compatibility with recent Intel and AMD platforms" and Intel handheld fixes; 3.9 has Panther Lake fixes | unofficial |
| Graphics | `CONFIG_DRM_I915=m` and `CONFIG_DRM_XE=m` (the 7.2.15 kernel config). Mesa 26.1.2 has iris; `vulkan-intel` and `intel-media-driver` are present | the desktop: likely; Game Mode on the N100: *check*, and not needed |
| Wired NIC | `CONFIG_R8169=m`; `rtl_nic/rtl8168h-2.fw` is in linux-firmware-neptune jupiter.20260910.1, 3.9.5's version (the jupiter-3.9 files database; verify on the A100) | likely |
| Disk | `CONFIG_BLK_DEV_NVME=m`, forced into the initramfs by the `steam-deck` hook (`sc:mkinitcpio/initcpio/install/steam-deck.in`, which also adds `vmd` and `spi-intel` "for non-Deck … mostly Intel") | yes |
| The backup drive | mybig512 is XFS (`DRIVE`'s `modprobe -q xfs`, `testvm/realworld/backshot1/backshot1:68`). `CONFIG_XFS_FS=m`, so the running SteamOS can mount it. `/mnt` is on the read-only root, so `DRIVE` falls back to `/run/bs1-backup` as on Aeon (`backshot1:64-68`) | yes: `xfs.ko` is in linux-neptune-72 7.2.15 (3.9.7); in 7.2.7 (3.9.5) it's *check* |
| Microcode | no `intel-ucode`; the command line carries `amd_iommu=off` and `amdgpu.*` from `jupiter-hw-support`'s `/etc/default/grub-steamos` | harmless: the firmware's microcode, AMD options ignored |
| Secure Boot | steamcl is unsigned | already off; *check* after any BIOS reset |
| Wi-Fi | MT7920 | irrelevant: wired only ([[rescue-wired-only]]) |
| TPM | none visible | dirlock's TPM protectors unusable, irrelevant |

### Installing it from the BackOtter stick (Decision 9)

A new `testvm/realworld/steamos-bootstrap.sh`, after the committed bootstrap scripts' pattern (`neon-bootstrap.sh`, `pika-bootstrap.sh`, `guix-bootstrap.sh`, `void-zbm-bootstrap.sh`; `testvm/realworld/backshot1/README.md:311, 396, 607`), with its rehearsal `steamos-bootstrap-qemu.sh` as theirs. The drive's `backshot1/steamos/` holds the script and Valve's system image `steamdeck-oobe-system-image-20260707.10-3.8.14.img.zst`, copied from Chopin's `/bkoscratch/rebuild-inputs/steamos3/` with its pinned SHA-256. Nothing outside `backshot1/` on mybig512 is touched. Run on the stick, detached:

    sudo bash /mnt/bs1-backup/backshot1/steamos/steamos-bootstrap.sh --detach S7XMNF0Y787118 thinkpad

It runs only our own code and the stick's tools. No SteamOS program runs on the stick.

**It refuses:**

- A SERIAL that isn't exactly one whole internal disk, or that is the drive's or the stick's disk.
- A stick booted without UEFI or with Secure Boot on.
- An image whose SHA-256 doesn't match.

**Its steps:**

1. `zstd -dc IMG | dd of=/dev/disk/by-id/…S7XMNF0Y787118 bs=4M conv=fsync`. This is a plain write. There is no NVMe sanitize as Valve's installer does, and the old data beyond 11 GiB stays until home grows over it.
2. `sgdisk -e`, which moves the backup GPT to the disk's end, so systemd-repart can grow home at first boot.
3. Reads the image's two rootfs fsids. If they are equal (*check*), it notes that the first update gives B its own, and BackShot1 can't start before that update (load refuses equal fsids).
4. Offline, into var-A's upper, the same seed the guest gets: sshd enabled, root's `authorized_keys` in home's offload (home is the 100 MiB stub here, until it grows), the fixed host key, the hostname `thinkpad`, a `deck` password hash from `backshot1/keys/` (*check* that the system image's user is `deck` and not `doorstop`), and the atomic-update keep file. No sddm change: the A100 keeps what Valve's first boot does (Decision 12).
5. The NVRAM entry: `efibootmgr -c -d DISK -p 1 -L SteamOS -l '\EFI\steamos\steamcl.efi'`, the same command `steamcl-install` makes. It deletes entries labelled `SteamOS` or naming the wiped disk's old partitions, and leaves `SteamOS` first in BootOrder.
6. Logs to `/run/bko-steamos/install.log`, copied to `backshot1/steamos/install-DATE-exitRC.log`. The last line is `BKO-STEAMOS-INSTALL-OK`.

**Then:**

1. **First boot, attended.** This goes first ([[front-load-manual-tests]]). The OOBE may ask for language, network and a Steam login (*check*), and may download and apply an OS update by itself before any login (*check*; the A100 risks). The person switches to desktop mode if the OOBE allows it without a login (*check*). If not, this needs the user's Steam account once, and that is the user's call.
2. `backshot1 wait-host` (the bootstrap seed already enables sshd).
3. One update to 3.9.x stable from desktop mode (`steamos-update`, over SSH), and a reboot into B. A is then 3.8.14.
4. `adopt`, after the update and not before. post-install keeps only keep-listed `/etc` upper files, and adopt writes more than its sshd drop-in: firewalld `--permanent` zones (`bs1-adopt:748-760`) and avahi's enable link (`:706-723`). An adopt before the update would be lost from B. A's upper keeps only the seed (sshd, root's key, the host key; jupiter-firewall allows ssh), which is enough for SSH on the other-slot boots. Whether A also needs adopt's mDNS is *check*; if it does, adopt runs once on A too, after one other-slot boot.
5. `/etc/bko-slot` marks in both uppers, as on the guest.

**The alternative, R3:** Valve's recovery image on a *spare* USB stick (not the rescue stick, not mybig512), and a person clicks "Wipe Device & Install SteamOS" at the screen. It's Valve's real installer, with per-install IDs (and `efi`/`var` labels), but it isn't remote.

### What adopt changes on SteamOS

- **Detection:** `is_steamos` in bs1-common (the partlabel roles and `ID=steamos`), beside `is_abroot` (`:172`).
- **`pkg_install`:** a guard like ostree's (`bs1-adopt:166-170`). On SteamOS it never runs pacman and never runs `holo-readonly disable`. It names the missing package and stops (Decision 8). Everything adopt uses should already be in the image: openssh, python, util-linux, efibootmgr, rsync, avahi and nss-mdns. S9 checks the list against 3.9.5's manifest.
- **sshd:** `systemctl enable --now sshd`. The symlink lands in the upper, keep-listed, so it survives updates. `allow_root_key_login` (`bs1-adopt:688`) writes an `sshd_config.d` drop-in, which adopt also adds to `etc/atomic-update.conf.d/bko.conf`, since the default keep list has only `ssh/*_key{,.pub}`.
- **Root's keys** land in `/root`, which is home's `.steamos/offload/root`. They persist across updates, and they're on `home`, which BackShot1 restores.
- **mDNS:** `start_mdns` (`bs1-adopt:706-723`) enables `avahi-daemon` if it's off (*check* the default).
- **Firewall:** `open_firewall` (`bs1-adopt:748-760`) already handles firewalld. jupiter-firewall's public zone allows ssh already, and adding mdns is harmless.
- **Hostname:** `/etc/hostname` in the upper (keep-listed).
- **LUKS:** none, and no change to `bs1-luks`: `luks_adopt` already says "no LUKS volume on $disk: nothing to do" (`bs1-luks:1343-1346`) before its family chain (`:1371-1377`), and `unlock_mode` already says none on a disk without LUKS. A dirlock-encrypted home stops the preflight, with a message naming it.
- **The other gates that become `|| is_steamos`** (each now ostree- or ABRoot-only):
  - `bs1-common:266` `internal_mounts`, and `:447` `make_tree`, which runs `mount_hints` only on ABRoot.
  - `bs1-backup:86` (`is_abroot || check_coverage`): `check_coverage` runs before the dump, and an untriggered `/efi` or `/esp` shows as autofs there and stops the run, so bs1-backup triggers both automounts first (a `stat` of `SteamOS/` in each), not only dump.
  - `bs1-backup:115` (the UPDATERS loop), `:275` (`unhinted_check`), `:321` and `:436` (preflight, then `mount_hints`, then `check_coverage`).
  - `bs1-verify:64` (the `--boot` gate), `:214`, `:249` and `:359`.

### The run

1. **Preflight** (a new `bs1-steamos preflight`, after `bs1-abroot`'s shape, `testvm/realworld/backshot1/bs1-abroot`):
   - `/` is `rootfs-A` or `rootfs-B` on the disk with `INTERNAL_SERIAL`, and os-release is in `SUPPORTED`.
   - Both slots are present and their fsids differ.
   - No `reboot_for_update`, no `holo-booted-slot-sync-trigger`, no factory-reset `*.cfg`, no `verity-X`, no dirlock policy, and atomupd and rauc are inactive.
   - `holo-readonly status` says enabled.
   - check_coverage passes through the three mount hints (`bs1-common:422`).
2. **Updates held:** the runtime masks above.
3. **Baseline**, adding `slots.txt`:
   - `holo-bootconf this-image` and `selected-image`, and `dump-config` for both slots.
   - Each slot's `manifest.json`.
   - The `btrfs property get … ro` of both rootfs.
   - The sha256 of every partsets file, `grub.cfg`, `grubx64.efi` and `steamcl.efi`.
   - `sfdisk -d`, `/proc/cmdline`, `findmnt`, and `lsattr -d` of home's `+F` directories.
4. **Dump and backup** through `backup_sources`. The size is est. ~12-15 GiB: two rootfs ~6-7 GiB of files each, deduplicated where they're equal, plus home's few GiB, onto mybig512's `backshot1/` repositories. bs1-backup records `slots.txt` before and after and fails the run if they moved (as ABRoot's before/after check, `bs1-backup:474-478`, and Bazzite's, `:468-472`).
5. **To the stick, restore, boot**, unchanged: `load --erase` of the disk with `INTERNAL_SERIAL` (bs1-restore resolves it from the serial, `bs1-restore:116`) `--through restore`, then `--through close`. These are the same disk and size, so home's size is the source's.
6. **Verify**, extended, in this order:
   1. Neither updater ran since boot.
   2. The bs1-manifest comparison with the STEAMOS rules.
   3. The required slot check: `this-image`, its buildid, `/etc/bko-slot`, the PARTUUID of `/`, and `ro=true`.
   4. `boot-attempts=0` after `holo-boot`.
   5. The partsets and GRUB sha256s equal the baseline's.
   6. The NVRAM entry exists.
   7. `/home/.steamos/offload/var/log/bko-holt` is there.
7. **The other slot boots** (Decision 7): `holo-reboot --reboot-other` over SSH, A's checks, then `holo-reboot --reboot-other` from A back to B and B's checks (the flag is sticky, so "back" is a second reboot-other, not a plain reboot).

Hands-free cycles (`backshot1 cycle`) work as for any system without LUKS, once the first run passes. The A100 already reaches the stick by BootNext (`README.md:162`).

### Risks on the A100

- **The OOBE may need a Steam login.** If the system image's first boot can't reach desktop mode without one, S10 needs the user once at the screen with their account. Otherwise it falls back to R3.
- **The OOBE may update by itself** (*check*). An OOBE image may download and apply an OS update during the first boot, before any login. Then the A→B update happens uncontrolled in S10 step 1: B gets whatever build is current, and the maker's update in step 3 may find nothing to do. That's acceptable if B ends up a different build from A and the bko-slot marks follow; the run records both buildids either way.
- **Intel is unsupported.** Something Deck-specific may misbehave on the N100:
  - The `amd_iommu=off` and `amdgpu.*` arguments.
  - `jupiter-*` units.
  - Audio and power daemons.
  - Game Mode on i915.
  - bko needs only a desktop, sshd and the NIC. A failure that blocks graphical.target would block `holo-boot` too, and with it unattended boots.
- **The 3.8.14 image on Intel.** 3.8 had Intel fixes, but the update to 3.9 must work from desktop mode on this hardware.

## Risks

- **Compression.** If the restore writes the rootfs uncompressed and it doesn't fit 5 GiB, the load fails at restore. S3 forces zstd. S1 measures the margin with `compsize`. rootfs-O's allocation profiles must reach load too (S2's `known` channel), or mkfs.btrfs's default DUP metadata eats into the same margin.
- **PARTUUIDs are load-bearing.** Any path that makes new PARTUUIDs (`--new-uuids`, `--new-machine`, a future `--fit-largest`) gives a disk whose initrd can't find root, and that drops to an emergency shell. v1 refuses all three. The check script catches a mismatch before reboot.
- **Equal fsids.** Two rootfs with one fsid (an unupdated system-image install) can't be read together reliably: GRUB's search finds either, and btrfs device scanning may confuse them (*check* the kernel's temp-fsid handling of cloned single-device filesystems, Linux 6.7+). dump records it, and load refuses it.
- **Unattended boots and boot counting.** A restored system that never reaches graphical.target is never marked good. At 3 failed boots steamcl shows a 30 s failsafe menu and boots the default; at 6 it pre-selects the other slot. On 3.9.5 both stages count, so that may be after 2 and 3 failed boots (*check*), and stage 2's `image-invalid` isn't cleared by a later good boot. The guest keeps a desktop session, and every verify checks `boot-attempts`.
- **The sticky `boot-other` flag.** Any tool that picks the slot from the confs without skipping `boot-other` picks the wrong one after an other-slot boot. vm-mount and the check script use steamcl's rule; load uses the dump's `self`.
- **`trusted.overlay.*` and `+F`.** Overlay xattrs are proven. Casefold `+F` on home directories is *check* in S6. A fork fix, if it's needed, comes first, as [[openbsd-restore-oom]] did.
- **The image churns.** Valve's recovery and system images are 3.8.14, and every build updates from there to the current stable. A rebuild of `steamos3` months from now lands on a newer B. The maker records both buildids.
- **No published checksums.** Our pinned SHA-256 is trust on first use over HTTPS. A changed image at the same URL fails the maker's pin.
- **Third-party copy of `repair`.** The facts above come from a third-party extraction. S1 compares it with the copy inside our downloaded recovery image (`/home/deck/tools/repair_device.sh`) before relying on it.
- **Live backups across an update.** An update writes the other slot raw, reformats the other var, and rewrites the ESP conf and maybe `steamcl` while a backup reads them, and `holo-post-update-shutdown` changes the other var again at the next shutdown. dump records `pending` (both trigger files) and skips its own mounts while rauc runs. BackShot1 masks both updaters, and the before/after check catches the rest.
- **The desync seed after a restore** is invalid and is regenerated at the next update: more download, same result (*check* in S5).
- **Old layouts.** 2022-era installs (ESP 64 MiB, efi 32 MiB) restore the same way, since load copies the table. No guest has that layout, so it is untested.

## Out of scope

- SteamOS 2 (Debian-based), HoloISO, ChimeraOS, SteamFork, and Bazzite's Steam-like builds. Bazzite is already supported; the others aren't SteamOS.
- dm-verity (`verity-X` partitions, `roothash`), the `dev` partset, and BIOS.
- dirlock (fscrypt) homes: refused in v1. Backing up their plaintext and dropping the policy is a later piece if the user wants it.
- `--new-uuids`, `--new-machine` and `--fit-largest` on SteamOS. Each would need `holo-partsets`, `grub-mkimage` and `update-grub` run in chroots of both slots, as `repair`'s `finalize_part` does (`repair:198-212`).
- Restoring a Steam Deck's SD card (an ext4 or exFAT library on `mmcblk0`): another disk, not the system's.
- A BackShot2 family for SteamOS.
- Re-downloading an image instead of backing up a slot.

## Shared lines (with the other new-distro sessions)

- `osrel.rs` `SUPPORTED`: its own row, appended; the second to land rebases (and appends to `supported_names`' assert string, never overwrites it).
- `redact.rs`'s word lists (the six `-a`/`-b` labels and `steamdeck`): edited once, re-read first.
- `classes.json`, the holttest1d README's 1Obscure row, `check-manifests.py`'s count and `t2lib/plan.py`'s counts: one session at a time, re-read first.
- **bs1-adopt's no-package-manager guard and the bs1-common `UPDATERS` lines** (Decision 8): KDE Linux claims the same pieces (docs/design/kde-linux.md:683-702), and GNOME OS shares them with KDE Linux. Whichever session lands first builds the guard generically (an image system: name the missing package and stop); the others add their detection to it and their updaters to `UPDATERS`.
- The generic btrfs top-level `ro` (S3) is new to bko and touches every btrfs plan's tail; a note goes into the shared folder before S3 starts.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| S1 | Fetch the recovery image and the system image into `/bkoscratch/rebuild-inputs/steamos3/` on Chopin (the user's OK first: 3.4 GB and 3.3 GB from steamdeck-images.steamos.cloud), and pin both SHA-256s. Compare our `repair_device.sh` with the third-party copy. `make-steamos.sh` by Decision 2's path (the SMBIOS-credential unit), the offline seed, the update into B and the two `/etc/bko-slot` marks; `rebuild-one.sh`; publish through vm-store (after Decision 13). Settle every *check* that the guest can answer: os-release and each slot's variant, the username, `/mnt` empty, `etc/fstab`, the marker paths, both slots' fsids in each image, the var labels, `compsize` margins, the grub.cfg search kind in 3.9, `+F` directories, avahi, graphical.target on VGA, a headless update, whether the OOBE updates by itself, what makes the `/efi` and `/esp` automounts and what grows home, whether a failed boot counts twice, holo-boot's name in 3.8, the format fallback on QEMU NVMe, and the 3.8.14 copies of `jupiter-biosupdate` and `jupiter-controller-update`. Write "What S1 found" here. **Done when** the guest boots alone into each slot, SSH works, `boot-attempts` stays 0, and `vm-update --try` is ok | 1.5 d |
| S2 | dump (High-effort subagent): the `SUPPORTED` row and test; detection by roles; `holt/src/steamos.rs` and `boot.steamos`; the eight `backup_sources` with the three hints and the automount trigger; the other slot's read-only mounts, named by partlabel, with rootfs-O's `ro` and allocation profiles passed to `btrfs_json` through `known`; `selected` by steamcl's rule with `boot-other`; `pending` with both trigger files; kernels of both slots; `redact.rs`'s words; `steamos_tree` scenarios (fresh, after update, after an other-slot boot, pending, factory reset, equal fsids, dirlock, both label forms). **Done when** the lib tests pass and no golden plan moves | 1.15 d |
| S3 | Generic btrfs (High): the top level's `ro` in dump and `boot_tail`; `compress-force=zstd` on the restore mount, scoped to a top level with `compress_zstd`, no compression property and no fstab `compress` option (so the 10 corpus dumps with `compress_zstd` don't move); the doesn't-fit plan problem; the top-level `ro` and compressed-size tests that SticTest1 only measures; tests on a non-SteamOS scenario too. **Done when** the tests pass and a re-plan of every btrfs corpus dump is unchanged except where FS_TREE `ro` was true | 0.5 d |
| S4 | load's SteamOS path (High): the mount stage from roles, S and O from the dump's `self`/`other`, the marker in `other/mod.rs` and `status.rs`, `steamos_boot` (note, check script, NVRAM, log under the offload, tail), the v1 plan problems. **Done when** the check script passes and fails on real fake trees (pass; a partsets PARTUUID changed; a grub.cfg naming the other fsid; a missing kernel; a factory-reset cfg; equal fsids) and the scenarios plan no `mkinitcpio`, `grub-install`, `update-grub` or chroot | 0.75 d |
| S5 | Guest tools: vm-mount's SteamOS mode and vm-chroot's overlay, `Linux.steamos` in in-guest.sh, pkgset, fingerprint, compare.py, `K-steamos` in guest_judge, check-image and check-after; one real update of a restored guest (the seed check). **Done when** `vm-update --try steamos3` is ok on two hosts and test_vm_update.py and test_guest_judge.py pass | 0.75 d |
| S6 | SticTest1 `steamos3`, both archivers, two hosts. **Done when** there are 0 FAIL on both label forms, with `+F` and overlay xattrs judged, and the top-level `ro` and the compressed sizes measured | 0.35 d |
| S7 | HoltTest1 1Obscure: the three boots as in-guest reboots within one `vm_up`, the source's own other-slot boot for its baseline, `steamos_source` and `steamos_verify` and their dispatch (`ht1.sh:1830, 2354`), judge.py's KEPT, EXPECTED and REQUIRED rules with judge-test.py, `ram-sizes.tsv`. **Done when** runs PASS on two hosts with 0 file differences outside EXPECTED | 0.75 d |
| S8 | Golden plan, corpus test (backup_sources equal the load plan's mount points), Test2 registration, p2 goldens. **Done when** two corpus builds hash equal, leakscan is 0 on two hosts and the Mac, and Test2's tests pass | 0.4 d |
| S9 | BackShot1 code: `is_steamos` and UPDATERS in bs1-common; every `is_ostree`/`is_abroot` gate listed under "What adopt changes" made to take `is_steamos` too (bs1-common, bs1-backup, bs1-verify), with the automount trigger before `check_coverage`; `bs1-steamos` (preflight, record, boot-check) with `tests/test_bs1_steamos.py` after `test_bs1_abroot.py`; bs1-adopt's guard, sshd, keep file; bs1-manifest's STEAMOS block; bs1-verify's slot checks and other-slot boots; `steamos-bootstrap.sh`; its rehearsal `steamos-bootstrap-qemu.sh` (as `neon-`/`pika-`/`guix-bootstrap-qemu.sh`): a blank NVMe with the A100's serial gets the system image (Decision 2's path 2, so no second guest build), then the seed, first boot, the update into B, adopt, a BackShot1 run and both other-slot boots; the README's SteamOS section. **Done when** the rehearsal PASSes on two hosts | 2 d |
| S10 | BackShot1 on the A100: the bootstrap from the stick, the attended first boot, the update into B, adopt, the run, the other-slot boots. **Done when** BackShot1 PASSes and both slots boot. Listing the Steam client's churn from this run is part of it | 0.75 d |
| S11 | Docs and memory: holt/README.md's supported list (`:81`) and a SteamOS bullet after ABRoot's (`:95-102`), `osrel.rs`'s doc comment, `docs/design/layload.md`'s boot stage (`:253`, beside its ABRoot paragraph `:418-437`) and the FS_TREE `ro`, HoltTest1's 1Obscure row and HoltTest1All, SticTest1's README, a memory entry. **Done when** it's written | 0.1 d |
| | *≈ 9.0 d in all; ≈ 6.25 d without BackShot1 (S9 and S10)* | |

S2 and S3 can start beside S1 from the source facts. Their golden plan (S8) waits for S1's dump. Each piece is committed on main when its tests pass and the user says to commit, and nothing is pushed until the user says so. Source-code changes go to High-effort subagents ([[effort-max-when-it-pays]]). Every build and test job goes through `testvm/pick-host` ([[split-chopin-cinqcent]]).

**Costs** (est.):

- S1: two downloads (6.7 GB, the user's OK first), a 20-40 min install, and an update download of ~1-2 GB.
- S9: the rehearsal's own install from the system image (no download beyond S1's) and one more update download of ~1-2 GB a rehearsal host.
- Disk: the qcow2 ~9-11 GiB a host and the inputs ~6.7 GB on Chopin.
- Queue time: S6 and S7 about 1.5 h a host.
- S10: about half a day of the A100. The user's time is the first boot at the screen (and a Steam login if the OOBE needs one) and the OKs.

## Estimate

**≈ 9.0 d (est.)**, as the table above:

- S1 guest: 1.5 d.
- Code: S2-S5, 3.15 d.
- Tests: S6-S8, 1.5 d.
- BackShot1: S9-S10, 2.75 d.
- Docs: 0.1 d.

The review of 2026-11-27 added 0.8 d: the other slot's btrfs facts in dump (+0.15), HoltTest1's in-place other-slot boots (+0.15), and BackShot1's missed gates and the bootstrap rehearsal (+0.5).

The main uncertainty is in S1 and S10. If Valve's installer can't run from a unit, path 3 (VNC keys) costs +0.25 d. If the OOBE needs a Steam login, S10 needs a person and may slip a day. If `+F` needs a fork fix, add est. +0.5 d before S6.

## Decisions for the user

1. **Which release and row:**
   - **Recommended:** one row, `("steamos", "3", "SteamOS 3")`, with the guest on the stable branch (3.9.x after its first update). It covers 3.8, 3.9 and 3.10 alike, and `supported()` matches only the major version anyway.
   - Or a guest on beta (3.9.7) or preview (3.10.x) too: +0.5 d each, more churn.
   - Or 3.8.14 only, never updated: B is then a copy of A, and the test is weaker.
2. **How the guest is built:**
   - **Recommended:** Valve's recovery image running its own `repair_device.sh all` unattended, from a unit handed in as an SMBIOS credential (nothing written into the image). That is Valve's real installer, with per-install PARTUUIDs and fsids; the A100's path is rehearsed separately in S9.
   - Or the system image written onto the qcow2: -0.25 d, the A100's own path, the same partition table, but fixed IDs and `efi-A`/`efi-B` labels.
   - Or goldboot-style VNC keystrokes: +0.25 d, fragile.
3. **Where the inactive slot sits in the tree:**
   - **Recommended:** as a chroot at `/mnt`, `/mnt/var`, `/mnt/efi`. That's post-install's own shape, and its mount points already exist in each rootfs.
   - Or two separate snapshots, one per slot: no new paths, but two restores and a tree that load can't plan in one pass.
   - Or a tree of the eight partitions by partlabel (`/rootfs-A`, ...): every path unlike the running system.
4. **How the rootfs slots are restored:**
   - **Recommended:** as files, like every other system, with each source fsid and PARTUUID kept, zstd forced, and `ro` set back. Everything that names them stays right, and the desync seed regenerates itself.
   - Or as raw block images of the two rootfs partitions stored in the backup: byte-identical, and the seed stays valid. But it's a new generic "image source" kind in dump, load and rustic-bko, ~10 GiB more per backup, and +1 d est.
5. **The three unmounted filesystems in a live backup:**
   - **Recommended:** dump mounts them read-only for its own reading and lists them in `backup_sources` with mount hints that the backup must apply (one rule, as for ABRoot).
   - Or docs only, no entries: easier to get wrong.
6. **Holding updates in BackShot1:**
   - **Recommended:** `systemctl mask --runtime atomupd.service rauc.service` for the run, `atomupd rauc` in UPDATERS, and a refusal while `reboot_for_update` or `holo-booted-slot-sync-trigger` exists, with bs1-backup's updater gate taking SteamOS. Nothing is written to the source's `/etc`.
   - Or the same plus Steam's own update setting turned off by you in Game Mode, if it has one (*check*).
   - Or no hold, only the before/after check.
7. **How the other slot is booted in tests:**
   - **Recommended:** `holo-reboot --reboot-other` over SSH, twice (there and back, since its flag is sticky), as in-guest reboots. It's SteamOS's own tool, and it writes only the ESP conf that every boot rewrites anyway.
   - Or the steamcl menu at the console (`console-keys.py` in QEMU, a person at the A100).
   - Or the default slot only.
8. **adopt on a read-only root:**
   - **Recommended:** never disable read-only or run pacman. Use only what the image ships, stop and name anything missing, and keep adopt's `/etc` files through a keep-list file; on the A100, adopt runs after the update into B, since post-install keeps only keep-listed files. The image is unchanged, and an update can't undo adopt.
   - Or `holo-devmode enable` and pacman for anything missing: it changes the rootfs that the KEPT rule protects, and the next update throws it away.
9. **How the A100 gets SteamOS:**
   - **Recommended:** `steamos-bootstrap.sh` from the BackOtter stick writes Valve's system image by serial, then makes the seed and the NVRAM entry. It's remote, it's Valve's own artifact, mybig512 is touched only under `backshot1/steamos/`, and it's rehearsed in QEMU in S9.
   - Or Valve's recovery image on a spare USB stick, with you clicking "Wipe Device & Install SteamOS" at the screen: Valve's real installer, but attended, and it needs a third stick.
   - Or no BackShot1: the guest only, -2.75 d.
10. **HoltTest1's group:**
    - **Recommended:** **1Obscure**, as Vanilla OS: an image system, not Arch as installed by its own installer.
    - Or **1A** beside Arch.
    - Or a new **1Immutable** with `vanilla3` and `kinoite44` moved into it.
11. **`/home/swapfile` (1 GiB):**
    - **Recommended:** backed up as is: faithful, and `swapfile.service` remakes it if the restore leaves holes.
    - Or excluded and remade at first boot: 1 GiB less per backup, and a file that differs after the restore.
12. **The guest's session:**
    - **Recommended:** a Plasma desktop autologin (the sddm drop-in), so graphical.target is reached on QEMU's VGA and `holo-boot` marks every boot good. The A100 keeps whatever Valve's first boot sets.
    - Or Game Mode (gamescope) on virtio-gpu: Valve's default. 3.9's notes claim "Improved support for running SteamOS as a VM guest with accelerated graphics (virtio-gpu, venus)" (Steam News appid 1675200, "SteamOS 3.9", 2026-10-23), and 3.8's say Desktop Mode now uses Wayland by default ("SteamOS 3.8", 2026-06-18). But Igalia says its recipe doesn't run gaming mode, so Game Mode under our QEMU is *check*.
    - Or multi-user with a drop-in that makes `holo-boot` wanted there: no display needed, but a unit change no real install has.
13. **The guest image in vm-store:**
    - **Recommended, if you judge the licence allows it (*check*, your call):** published to the private store on Bigfoot like every guest. It stays on your own machines and is never redistributed, but it is a modified, stored copy, and the EULA grants use "solely for your personal use" and forbids copying or modifying the image (§1.1, §1.2(a); the Guests section).
    - Or rebuilt on each host from the rebuild inputs, never stored: +30-40 min a host per rebuild.

## Open questions

Each is a *check* above, gathered here with the milestone that settles it:

- os-release's `VERSION_ID`, `VARIANT_ID` and `BUILD_ID` in 3.9, each slot's manifest variant, and the default hostname and username (`deck` or `doorstop`) (S1).
- Whether the system image's rootfs-B shares rootfs-A's build fsid, what its var labels are, whether its efi-B is populated, and whether its first boot makes an NVRAM entry (S1, S9).
- Whether 3.9.5's grub.cfg searches by fs UUID or by PARTUUID (S1). The check script accepts both.
- Whether a 3.8.14 install updates from desktop mode without a Steam login, whether the OOBE lets desktop mode start without one, and whether the OOBE updates by itself before any login (S1, S10).
- Whether 3.8.14's systemd reads the SMBIOS `systemd.extra-unit.*` and `systemd.unit-dropin.*` credentials (S1).
- Whether the 3.8.14 image's own `jupiter-biosupdate` and `jupiter-controller-update` exit 0 off Valve hardware (S1).
- What makes the `/efi` and `/esp` automounts and what grows home's filesystem, since systemd ignores those options in native units; whether systemd-repart runs every boot; whether avahi-daemon is enabled by default (S1).
- Whether a failed boot counts twice on 3.9.5 (both stages increment `boot-attempts`), putting the menu at 2 failed boots and the switch at 3 (S1).
- Whether the ESP and efi automounts stay mounted while bound into a backup tree (S2).
- Whether rustic-bko keeps ext4 `+F`, and which home directories carry it (S6).
- Whether the rootfs's uncompressed size exceeds 5 GiB, whether Valve's build forced compression, and whether the top level has a compression property (S1, S3).
- Whether the `xfs` module is in 3.9.5's 7.2.7 kernel (it is in 7.2.15) (S9, from the package files; S10 on the A100). `rtl8168h-2.fw` is settled: it's in 3.9.5's linux-firmware-neptune.
- Whether the unadvertised build images (`steamdeck-BUILD-VERSION.img.zst`, such as 3.9.5's) carry `repair_device.sh` (only if a 3.9.5 slot A is wanted).
- Whether A needs adopt's mDNS for the other-slot boots on the A100 (S10).
- Whether the EULA allows a modified guest in the private vm-store (the user: Decision 13).
- Whether Game Mode starts on the N100's i915 (S10, recorded only), and under QEMU (not needed: Decision 12).
- What the 3.9 names are for `steamos-update` and `steamos-bootconf` (`holo-update`'s source isn't in the mirrors) (S1).
- When the ESP grew from 64 to 256 MiB and the efi partitions from 32 to 64 MiB (not needed: load copies the table).

Settled by the 2026-11-27 review: `set-mode booted` doesn't clear `boot-other` (it's sticky; the tests reboot-other twice); GPT attribute bits are 0 in both images; `/mnt` exists in rootfs; the `client.conf` marker path; untriggered automounts are absent from `storage.mounts`; `rtl8168h-2.fw` is in 3.9.5.

This spec awaits the user's approval.
