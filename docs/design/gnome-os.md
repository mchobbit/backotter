# GNOME OS support (systemd-sysupdate, verity /usr A/B, TPM2 FDE)

2026-11-27. Asked for by the user: support GNOME OS, GNOME's own
image-based system. It has no package manager. Its `/usr` is a signed,
read-only erofs image under dm-verity, kept in two GPT slots (A/B) that
systemd-sysupdate fills. Its root is btrfs, and it is encrypted with LUKS2
sealed to the TPM2 whenever a TPM2 is present. It is written the way
Vanilla OS (docs/design/vanillaos.md, A/B), Bazzite (docs/design/bazzite.md,
immutable) and Ubuntu Core (docs/design/ubuntu-core.md, TPM-sealed FDE)
were, and borrows Aeon's TPM notes (docs/design/aeon.md).
**Approved by the user 2026-11-27, every recommendation (R1..R12, the last section); building waits until the user says to proceed.** Nothing is built. The
recommendations (R1..R12) and the open questions (Q1..Q11) are at the end.
Revised the same day after a code review and a fact review: the GNOME OS
facts are now GNOME 51's own branch, not master's (below).

**Conventions.** est. marks an estimate. *Check* marks a fact the sources
don't settle. Each one is settled on the first guest (GN1) before the code
that depends on it. UNCONFIRMED marks a claim that no source read today
proves; each is also a *check*. GNOME OS facts were read on 2026-11-27 from:
- os.gnome.org: https://os.gnome.org/, https://os.gnome.org/docs/install.html,
  https://os.gnome.org/docs/using.html,
  https://os.gnome.org/docs/manual-dual-boot.html,
  https://os.gnome.org/docs/print.html; the release post,
  https://blogs.gnome.org/adrianvovk/2026/10/16/gnome-os-preview;
- **gnome-build-meta branch `gnome-51` at db69d872 (2026-11-23)**, which
  builds GNOME OS 51 (`branch: '51'`, `sysupdate-branch: gnome-51`,
  GBM51:project.conf:206,213). "GBM51:path:line" below means
  `https://gitlab.gnome.org/GNOME/gnome-build-meta/-/blob/gnome-51/path#Lline`.
  master (GNOME 52 in development) at b54fd30f is "GBM:path:line"
  (`.../-/blob/master/path#Lline`), cited for a file read only on master
  (not diffed against gnome-51; a *check* on GN1) or for what master
  changes. The two differ in exactly
  the TPM files this spec leans on: the installer daemon's `main.rs`,
  `50-root.conf`, `signed-boot.bst`, `80-pcrlock.preset`, `deps.bst` and
  `run-secure-vm.sh`;
- the sysupdate feed, by HTTP HEAD and its `SHA256SUMS` (1,053 lines),
  https://1270333429.rsc.cdn77.org/gnome-51/sysupdate/SHA256SUMS;
- systemd's man pages:
  https://www.freedesktop.org/software/systemd/man/latest/systemd-pcrlock.html,
  https://www.freedesktop.org/software/systemd/man/latest/systemd-cryptenroll.html,
  https://www.freedesktop.org/software/systemd/man/latest/systemd-stub.html,
  https://www.freedesktop.org/software/systemd/man/latest/repart.d.html,
  https://www.freedesktop.org/software/systemd/man/latest/sysupdate.d.html,
  https://www.freedesktop.org/software/systemd/man/latest/updatectl.html;
  and the DPS,
  https://uapi-group.org/specifications/specs/discoverable_partitions_specification/;
- freedesktop-sdk 26.08.4 (GNOME 51's kernel and systemd), at
  https://gitlab.com/freedesktop-sdk/freedesktop-sdk/-/tree/freedesktop-sdk-26.08.4;
- KDE Linux at invent.kde.org/kde-linux/kde-linux master 645a4bd9, for
  the shared parts only.

Only HTTP headers and text files were fetched (into the session's
scratchpad). No image was downloaded or installed. Code references are to
local `main` at d4aea22 (the ones the review touched rechecked at 1d377cb).

## Summary

- **The release.** GNOME OS "Preview" (the stable channel) tracks
  **GNOME 51** and ships rolling builds within 51.x: the gnome-51 feed holds
  51.0, 51.0-1..-17, 51.1-1..51.1-66 (with gaps), 51.beta-31 and 51.rc-2,
  newest **51.1-66** today (feed `SHA256SUMS`). The ISO is pinned by its
  versioned URL,
  https://1270333429.rsc.cdn77.org/tag/51.1/gnome_os_51.1-x86_64.iso
  (3,741,377,024 bytes, Last-Modified 2026-10-18); the
  `os.gnome.org/download/stable/51/gnome_os_x86_64.iso` link is a moving
  alias (302 to `/download/51.1/...`, then to the CDN). A nightly channel
  exists too. The front page says "Forward compatibility is not
  guaranteed" (https://os.gnome.org/), and the release post says updates
  are not promised in perpetuity
  (https://blogs.gnome.org/adrianvovk/2026/10/16/gnome-os-preview). So a
  guest is per GNOME major, as `sculpt2610` is per Sculpt release (R1).
- **The layout is fixed by GNOME's installer** (GBM51:files/gnomeos/live/
  proto-installer/daemon/main.rs:257-335): an ESP, usr-verity A and usr A,
  usr-verity B and usr B (empty at install), and a btrfs root on the rest
  of the disk, as partitions 1-6. `/etc`, `/var` and `/home` all live on
  that root. There are no signature partitions: the signed UKI pins `/usr`
  by `usrhash=`.
- **The /usr slots can only come back as bytes.** A file-level restore of
  an erofs image can't reproduce its dm-verity root hash, and the UKI is
  signed over that hash. bko-holt has raw copies for LVM LVs
  (`holt/src/qubes.rs:271`, `holt/src/load/rawvol.rs:1-10`) and, for
  non-Linux systems only, an unmounted ESP's bytes up to 64 MiB
  (`esp_image`, `holt/src/other/mod.rs:427-482`,
  `holt/src/load/writetable.rs:383-425`). **Raw GPT partitions of GiB
  size are new generic work** (GN3), and so is putting their images into
  an online backup: today only `ht1-offline.sh` makes raw images
  (`testvm/holttest1d/ht1-offline.sh:347-360`, `:453-455`).
- **The root is TPM-sealed LUKS2.** `load` today makes a new header, which
  loses the TPM2 token (`holt/src/load/commands.rs:2339`). **Recommendation
  (R4): keep the LUKS2 header**, as Ubuntu Core does
  (`holt/src/load/commands.rs:2517-2531`). On the same machine the token
  should then unseal again with no one typing (UNCONFIRMED: it rests on
  pcrlock's predictions, below). On a new TPM the recovery key is typed
  once, and the user re-seals with GNOME's documented three commands
  (51's form, without `--tpm2-public-key-policyref`).
- **The boot stage regenerates nothing, and needs its own arm.** systemd-boot,
  shim, the UKIs and their boot counters all come back with the ESP. The
  generic boot stage can't serve: it needs the restored `/etc/fstab` and a
  working `chroot` (`holt/src/load/commands.rs:6097`, `:6639-6649`), and
  GNOME OS has no fstab and no `/usr` in the restored tree. So GNOME OS gets
  an early-return arm, as `abroot_boot` and `snapcore_boot` have
  (`holt/src/load/commands.rs:6079-6094`), with its own mount set and
  restore marker. The arm checks that every UKI's `usrhash=` names a
  restored usr-verity pair, and makes the NVRAM entries.
- **Most of the new work is shared with KDE Linux** (sysupdate detection,
  UKI inventory, the update hold, the bs1 and guest tools). R11 proposes
  building those once, in a generic `sysupdate` module.
- **BackShot1 on the A100 is proposed** (GN11), after a `bs1-qemu.py`
  rehearsal on the guest. The install is by hand at the console, front-loaded
  (R9).
- ~11 d (est.) with BackShot1; ~8.75 d without it.

## Why

- **The third immutable design.** ostree has one root and a repository.
  ABRoot has two whole root filesystems. GNOME OS has two **block images**
  of `/usr`, chosen by the kernel command line's `usrhash=`. No guest
  tests a system whose OS is not files.
- **Partitions that must be bytes.** Every restore today makes filesystems
  and fills them with files. A dm-verity image is the first case where
  that can't work. The same raw-partition path also serves any future
  verity system (KDE Linux plans verity:
  https://community.kde.org/KDE_Linux/Verity).
- **TPM2 by default.** GNOME OS encrypts automatically when
  `systemd-analyze has-tpm2` reports full support and
  `GNOMEOS_INSTALL_DISABLE_TPM` is unset
  (GBM51:files/gnomeos/live/proto-installer/daemon/main.rs:648-656), and
  writes `Encrypt=off` otherwise (main.rs:261-270).
  Aeon is the only other guest sealed with systemd-cryptenroll, and its
  A100 run was in passphrase mode (memory, [[aeon-backshot1]]).
- **No crypttab, no fstab.** The root is found by GPT type and label
  (systemd-gpt-auto). Every tool that reads crypttab or fstab (bs1-luks's
  "own" test, vm-mount, and load's mount stage, boot stage and restore
  check) sees nothing.
- **No package manager at all.** Ubuntu Core has snaps, Guix has its
  store. GNOME OS has a version string, sysexts and Flatpaks.

## What GNOME OS is (as far as bko cares)

| | GNOME OS 51 (systemd-sysupdate) | Nearest supported system |
|---|---|---|
| Release | "Preview" (stable), announced 2026-10-16 (https://blogs.gnome.org/adrianvovk/2026/10/16/gnome-os-preview); rolling builds within 51.x, newest 51.1-66 (feed `SHA256SUMS`); 50.6 and a nightly also served. Feed `https://1270333429.rsc.cdn77.org/gnome-51/sysupdate/`: the directory itself is 404 (no index); `SHA256SUMS` (200) and `SHA256SUMS.gpg` (95 B) are served, and sysupdate checks them against `/usr/lib/systemd/import-pubring.pgp` (`Verify=` defaults to yes, sysupdate.d(5)). Kernel: linux 7.3 per GNOME 51's freedesktop-sdk 26.08.4 junction (https://gitlab.com/freedesktop-sdk/freedesktop-sdk/-/blob/freedesktop-sdk-26.08.4/elements/include/linux.yml#L10; the ISO's own kernel UNCONFIRMED); systemd 262.2 (GBM51:elements/core-deps/systemd-base.bst, `ref: v262.2`) | Aeon (rolling, systemd-repart) |
| os-release | `ID=org.gnome.os`, `VERSION_ID="@SDK_BRANCH@"`, `IMAGE_VERSION="@IMAGE_VERSION@"` (GBM51:files/os-release/os-release.sysupdate.in:3-4,11). Built with branch 51, so VERSION_ID is very likely `51` and IMAGE_VERSION the build, e.g. `51.1-66` (the mapping is UNCONFIRMED). Version parsing and redaction must take `51.1-66` | |
| ISO | `gnome_os_51.1-x86_64.iso` at `https://1270333429.rsc.cdn77.org/tag/51.1/`, 3,741,377,024 bytes. **No checksum or signature file found**: `SHA256SUMS`, `.sha256`, `.sig` and `.asc` under `tag/51.1/` are all 404, and neither the front page nor the release post names one (UNCONFIRMED that none exists anywhere). The sysupdate feeds have a signed `SHA256SUMS` + `SHA256SUMS.gpg` | Vanilla (sha256 only) |
| Disk | GPT, made by `systemd-repart --empty=require` (GBM51 main.rs:408-412, 455-475). Partitions 1-6, from the definitions `10-esp` … `50-root` (the file prefixes are not partition numbers: repart appends "in the order defined by their names", https://www.freedesktop.org/software/systemd/man/latest/repart.d.html): p1 ESP vfat 500M-1G; p2 usr-verity `gnomeos_usr_v_<ver>` 275M; p3 usr `gnomeos_usr_<ver>` 4G; p4 usr-verity `gnomeos_usr_v_empty` 275M; p5 usr `gnomeos_usr_empty` 4G; p6 root, label `root`, the rest (main.rs:272-332). repart's M and G are MiB and GiB, so the fixed area is ≈9.0-9.5 GiB. The ESP differs by install path: the installer's 500M-1G, upstream's raw pre-install (R2 path 1) a fixed 1G (GBM:utils/repart.raw.d/10-efi.conf), secure-repart-config 500M (GBM:files/secure-repart-config/main/10-efi.conf). One UKI is 240,740,376 bytes (51.1-66 on the feed), so two (`InstancesMax=2`) nearly fill a 500 MiB ESP. The numbering is settled on GN1 | Ubuntu Core (fixed partitions) |
| /usr | erofs (`mount.usrfstype=erofs`, GBM51:elements/gnomeos/signed-boot.bst:34) under dm-verity, read-only, `panic-on-corruption`. Per the DPS, the data partition's UUID is the first 128 bits of the root hash and the verity partition's the last 128 bits, so the UKI finds its own pair. The x86-64 usr-verity type is `77ff5f63-e7b6-4633-acf4-1565b864c0e6` (DPS) | none |
| Root | btrfs (GBM51:files/secure-repart-config/main/50-root.conf:5), persistent. Holds `/etc`, `/var`, `/home`. `BlockDeviceReplace=/` copies the live session's btrfs into it, so the live user carries over (GBM51 main.rs:323-332). With `Encrypt=`, repart derives the LUKS2 UUID from the partition's UUID (repart.d(5), `Encrypt=`) | Aeon (btrfs root) |
| Encryption | `Encrypt=key-file+tpm2`, `Discard=yes`, `VolumeName=root` when a TPM2 exists; `Encrypt=off` without one (GBM51 main.rs:257-270, 323-332). **No `TPM2PublicKeyPolicyRef=` on 51**: that is master's (GBM:files/secure-repart-config/main/50-root.conf:6), and repart.d(5) for systemd 262 documents none. Keyslot 0 is the recovery key with a `systemd-recovery` token (GBM51 main.rs:494-510): 32 random bytes as 64 modhex characters in eight groups of eight joined by `-` (main.rs:566-568, 602-618), written to the key file with no newline (main.rs:429-437). systemd takes recovery keys "wherever passphrases are accepted" (systemd-cryptenroll(1), `--recovery-key`). The TPM2 slot is **one** `systemd-tpm2` token. On 51 the UKI's PCR signatures are made with two keys, `tpm2-pcr` (enter-initrd) and `fstab-tpm2-pcr` (enter-initrd:leave-initrd), with no policyref (GBM51:elements/gnomeos/signed-boot.bst:125-143); where pcrlock works, the same token also carries a pcrlock policy (main.rs:212-255, 449-451). Whether both parts must hold is UNCONFIRMED. No passphrase option exists: the D-Bus method is `Install(device, oem_install)` and returns the key (main.rs:570-635) | Aeon default mode, `uc24-fde` |
| pcrlock | `make-policy --location=770 --pcr=0+1+2+3+4+5+7+11+14` in the installer (GBM51 main.rs:228-235); `...+14+15` in GBM51:files/gnomeos/systemd-pcrlock-workaround.conf:7, and in GNOME's re-seal recipe. `--location=770` is how far into the sorted components the prediction goes (the initrd), not an NV index; the NV index is picked automatically unless `--nv-index=` is given (systemd-pcrlock(8)). pcrlock leaves out of the policy any PCR whose log holds unrecognised measurements (same page). **The PCR set an installed system really uses is UNCONFIRMED**: GN1 reads `systemd-pcrlock predict` and `pcrlock.json`. Locks on 51: **enabled** machine-id and secureboot-authority; **disabled** file-system, firmware-code, firmware-config, secureboot-policy and every other (GBM51:files/gnomeos/systemd-presets/80-pcrlock.preset:6,9,13,17,22,26,29). master also enables firmware-code and secureboot-policy | Aeon's PCRs 4,5,7,9 |
| Raw-image install | First-boot repart in the initrd; `enable-encryption.service` has `ConditionSecurity=tpm2`, `disable-encryption.service` deletes `Encrypt=` without one (GBM:files/gnomeos/generate-initramfs/modules/50-repart/enable-encryption.service:4, disable-encryption.service:4,16). The recovery password there is the literal `ihavenotsetarecoverykey` (GBM51:files/gnomeos/generate-initramfs/modules/50-repart/gen-recovery-key.sh:11). That repart runs with `--discard=no` (GBM:files/gnomeos/generate-initramfs/modules/50-repart/gnomeos.conf:3,13), and B has no `CopyBlocks=` or `Format=`, so on a used disk B may hold stale old data, not zeros. A raw-image install gets a TPM2 slot **without** pcrlock until re-enrolled (GNOME's docs say to check `tpm2-pcrlock: true`, https://os.gnome.org/docs/manual-dual-boot.html) | |
| Boot | shim **signed by GNOME's own key, not Microsoft's** → systemd-boot → UKI (GBM51:elements/gnomeos/signed-boot-common.bst:47-58). shim at `EFI/org.gnome.os/shimx64.efi`, copied byte for byte to `EFI/BOOT/BOOTX64.EFI`, with shim's fallback `EFI/BOOT/fbx64.efi` beside it (signed-boot-common.bst:54-58). **The installer makes no NVRAM entry**: its daemon has no `bootctl` or `efibootmgr` call, only `efi.automount` (GBM51 main.rs:337-345). The first boot from the disk runs the fallback, which creates the GNOME OS entry (GNOME's dual-boot recipe: "it is creating the boot entry for GNOME OS", https://os.gnome.org/docs/manual-dual-boot.html); that it does so after an installer install is UNCONFIRMED. Install docs say turn Secure Boot off (https://os.gnome.org/docs/install.html). The ESP carries `loader/keys/{auto,private}` for systemd-boot's enrolment in setup mode (signed-boot-common.bst:17-36) | Aeon (sdbootutil, shim) |
| UKI | ukify, signed, `.pcrsig` for both initrd phases, a "Safe mode" profile (its own `.cmdline`, GBM51:elements/gnomeos/signed-boot.bst:105-107, joined at :142), addons in `gnomeos_<v>.efi.extra.d/` (apparmor, live, nvidia, signed-boot.bst:62-76). Cmdline: `rw quiet splash mount.usrflags=ro mount.usrfstype=erofs systemd.verity_usr_options=panic-on-corruption lockdown=integrity ... usrhash=<roothash>`; no `root=`, no `roothash=` (signed-boot.bst:28-39, 128) | ZFSBootMenu (a UKI) |
| Initramfs | inside the UKI; BuildStream "generate-initramfs", systemd stage 1, not dracut nor mkosi (GBM:elements/gnomeos/initramfs/image.bst:16-28). Modules listed: btrfs, erofs, exfat, squashfs, overlayfs, scsi, nvme (GBM:files/gnomeos/generate-initramfs/modules/50-kmod/module.sh). The kernel is freedesktop-sdk's, from `make defconfig` (https://gitlab.com/freedesktop-sdk/freedesktop-sdk/-/blob/freedesktop-sdk-26.08.4/elements/components/linux.bst#L67), and x86_64_defconfig builds in USB storage and ext4 (https://github.com/torvalds/linux/blob/master/arch/x86/configs/x86_64_defconfig); fdsdk's `fdsdk-config.sh:1604-1605` makes XFS a module and removes neither. So **usb-storage and ext4 are built in** (UNCONFIRMED on the shipped `.config`), and xfs is absent from the initrd | `prebuilt` (AerynOS) |
| /etc | an overlay: the image's own `/etc` ships as the confext `/usr/lib/confexts/gnomeos-base-config` (GBM51:elements/gnomeos/usr/layer.bst:82-85 per the fact review; read here on master, GBM:elements/gnomeos/usr/layer.bst:82-88), and the confext is **mutable** (`[ConfExt] Mutable=yes`, GBM51:files/gnomeos/confext/mutable.conf), with `/var/lib/extensions.mutable/etc -> ../../../etc` (GBM:files/gnomeos/confext/mutable-etc.conf:4-6). So `/etc` is always an overlay of the base confext (in `/usr`) with the root's `/etc` as its writable upper, and the root's `/etc` holds only the changes **plus whiteouts**. Factory copy in `/usr/share/factory/etc` (GBM:elements/gnomeos/usr/layer.bst:55-79) | ABRoot's /etc overlay |
| Updates | `updatectl update` or GNOME Software (`-Dsystemd-sysupdate=true` and `-Dfwupd=true`, GBM:elements/core/gnome-software.bst:45-52). Transfers: usr-verity, usr (ReadOnly), the UKI into `/EFI/Linux` with `TriesLeft=3 InstancesMax=2` (GBM51:files/sysupdate/90-gnomeos-kernel.transfer:17-19), sysexts into `/var/lib/extensions/` (GBM:files/sysupdate/40-gnomeos-devel.transfer:3,14-16). Optional features beyond devel: codecs-extra, nvidia-driver (nvidia-modules and nvidia-runtime sysexts, an nvidia addon), snapd+apparmor and debug, each with its confext on the feed (https://os.gnome.org/docs/using.html; feed `SHA256SUMS`). One update is ~3.2 GB: for 51.1-66, `usr` 2,888,447,545 B + `.verity` 27,381,760 B + UKI 240,740,376 B (HEAD on the feed); the release post says a full image every time. **The timers ship disabled** (GBM51:files/gnomeos/systemd-presets/50-sysupdate.preset:1-2) | vanilla-updates |
| Versions kept | 2; the older is picked in the boot menu (https://os.gnome.org/docs/using.html). A slot sysupdate vacates is labelled `_empty` (sysupdate.d(5)); the installer's `gnomeos_usr_empty` instead matches `gnomeos_usr_@v` as version "empty" (GBM51:files/sysupdate/81-gnomeos-usr.transfer:14; how sysupdate ranks it is UNCONFIRMED) | ABRoot's 2 roots |
| homed | **not used by default** (https://os.gnome.org/docs/print.html; GBM docs/hardening.md:15) | |
| SSH | openssh ships (GBM51:elements/gnomeos-deps/deps.bst:82) but **no preset enables sshd**. GNOME's own VM script starts it with `systemd.wants=sshd.service` ("We need to start sshd to create host keys") and passes the `ssh.ephemeral-authorized_keys-all` credential, then SSHes over vsock (GBM51:utils/run-secure-vm.sh:416-418, 472-485). firewalld zone "lax" allows ssh (and all ports 1025-65535) (GBM:files/firewalld/lax.xml) | |
| Tools in the image | rsync, git, jq, podman, toolbox, distrobox, btrfs-progs, nvme-cli, mokutil (GBM51:elements/gnomeos-deps/deps.bst:63-114), python3 via firewalld (GBM:elements/gnomeos-deps/firewalld.bst:20-21). e2fsprogs and parted are in master's deps.bst, not 51's (whether 51 has them by another element: UNCONFIRMED). **No efibootmgr. Neither avahi nor the `cryptsetup` command is listed** in 51's deps.bst (whether another element brings them: UNCONFIRMED; GN1 checks) | |
| Install | live ISO → gnome-initial-setup → "Install now" → pick an empty disk (https://os.gnome.org/docs/install.html). Dual boot unsupported | |

**The B slot of a fresh install is empty.** It is labelled
`gnomeos_usr_empty` and `gnomeos_usr_v_empty` (a slot sysupdate vacates
later is `_empty`). "Empty" is a label, not zeros: nothing writes B at
install, so it holds whatever the disk held. The "previous version" boot
entry exists only after one `updatectl update`. As for Vanilla, the guest is
given one update so both slots hold an image (GN1).

## What works today, and what breaks

**`bko-holt dump`:**

- `SUPPORTED` has no `org.gnome.os` row (`holt/src/osrel.rs:166`), so dump
  refuses (`check_supported`, `holt/src/osrel.rs:350`).
- **The partition table is kept exactly.** `sfdisk_script` writes each
  partition's `type=`, `uuid=`, `name=` and `attrs=`, the label-id and, on
  a same-size disk, last-lba (`holt/src/load/commands.rs:795`). `gpt_attrs`
  keeps bits 0-2 and 48-63 (`holt/src/load/commands.rs:773`). That covers
  every GPT name sysupdate matches and every PARTUUID usrhash leads to.
- **The type names.** `holt/src/parttable.rs:605-634` knows root
  (`:607`) and usr (`:616`) but not usr-verity or usr-verity-sig. That is
  cosmetic: the GUIDs are still written. The x86-64 usr-verity GUID is
  `77ff5f63-e7b6-4633-acf4-1565b864c0e6` and usr-verity-sig's is
  `e7bb33fb-06cf-4e81-8273-e543b413e2e2`
  (https://uapi-group.org/specifications/specs/discoverable_partitions_specification/).
- **dm-verity is misread as crypt.** `dm_json` classifies dm devices by
  uuid prefix, and anything `CRYPT-` becomes target `crypt` with
  `crypt_type` the next word (`holt/src/report.rs:541-546`).
  libcryptsetup names a verity device `CRYPT-VERITY-...`
  (https://gitlab.com/cryptsetup/cryptsetup/-/blob/main/lib/libdevmapper.c#L1240-1241),
  so `/usr`'s device is target `crypt`, `crypt_type: VERITY`. Load would
  then plan it as LUKS and refuse it: "doesn't hold a LUKS header in the
  dump" (`holt/src/load/commands.rs:2257-2263`).
- **erofs is unknown.** `grep -i erofs holt/src` finds nothing. If dump
  reports `/usr` as an erofs filesystem, load refuses it: "isn't a
  filesystem bko-holt load can make" (`holt/src/load/commands.rs:4264-4272`).
- **The verity hash partitions too.** blkid names a verity hash partition
  `DM_verity_hash` (UNCONFIRMED on the guest), and format's catch-all
  refuses any content type it doesn't know, with the same message
  (`holt/src/load/commands.rs:4264-4272`). So they take the raw path as
  the usr partitions do.
- **Raw copies are LVs only** (besides the non-Linux `esp_image`, above).
  `raw_volumes` keeps only `dm.target == "lvm"` (`holt/src/qubes.rs:286-290`).
  A plain GPT partition with unknown content is made empty. A raw source
  is `from: null`, its image copied into the tree by whoever runs the
  backup (`cp --sparse=always SRC TREE/.bko-holt/raw/...`,
  `holt/src/qubes.rs:380-397`).
- **The ESP is an automount at `/efi`.** The installer starts
  `efi.automount` (GBM51 main.rs:337-345). dump counts `autofs` as a
  pseudo filesystem (`holt/src/mounts.rs:91`, `is_layout_mount` `:105`), so
  if the vfat ESP has been idle-unmounted when dump or the backup runs, it
  is not a backup source, and "the ESP comes back file by file" fails.
  gpt-auto's idle timeout is UNCONFIRMED
  (https://www.freedesktop.org/software/systemd/man/latest/systemd-gpt-auto-generator.html).
- **The loader is read right only by a side door.** The installer makes no
  NVRAM entry (above). shim's fallback then makes one at the first boot
  from the disk, and the firmware boots `\EFI\org.gnome.os\shimx64.efi`:
  `shim = true` (`holt/src/boot.rs:1232-1233`), and LoaderInfo
  "systemd-boot ..." sets the name to systemd-boot (`holt/src/boot.rs:1288-1289`).
  If instead the firmware booted `\EFI\BOOT\BOOTX64.EFI`, shim is found only
  because that file is a byte copy of `*/shimx64.efi`
  (`holt/src/boot.rs:1258-1270`), or because shim's MOK variables are set
  (`:1297-1300`). If BootCurrent's device path has no `File()` node (a
  firmware auto-entry for the disk), `current_file` is None
  (`holt/src/boot.rs:1632-1642`), `via_shim` (`:1410`) is false unless the
  MOK variables are set, and load takes the plain arm: `chroot T bootctl
  install`, which would put an unsigned systemd-boot in place and fails
  without `/usr` (`holt/src/load/commands.rs:6521-6531`). So GN2 names
  GNOME OS's loader explicitly (shim at `EFI/org.gnome.os/shimx64.efi`,
  systemd-boot behind it) instead of relying on BootCurrent. *Check* on GN1
  which entry the guest booted.
- **The UKIs aren't listed.** `systemd_boot_json` reads `loader.conf` and
  `loader/entries` only (`holt/src/boot.rs:458`). A UKI parser already
  exists for ZFSBootMenu: `pe_sections` and `read_uki`, with `.osrel` and
  `.cmdline` (`holt/src/zfsbootmenu.rs:65`, `:101`, `:163`). It doesn't
  parse `.pcrsig`; nothing needs it.
- **No package database.** `installed_packages` returns an empty list
  (`holt/src/boot/packages.rs:395-399`). Package-keyed arms (dracut, the
  shim package, systemd-boot's package) don't fire. That is fine here, as
  for Guix and Ubuntu Core.
- **The initramfs is `generator: null`**, not `prebuilt`
  (`initramfs_json_esps`, `holt/src/boot.rs:1028`). It needs a "prebuilt:
  inside the UKI" answer, and not as a nicety: in load a null generator
  doesn't skip the step but falls through to `chroot T update-initramfs -u
  -k all` (`holt/src/load/commands.rs:7332`); only `prebuilt` returns None
  (`:7283`).
- **LUKS2 headers can be kept.** `luksheader::capture` and `kept`
  (`holt/src/luksheader.rs:165`, `:257`) by `dump --keep-luks-header DEV`
  (`holt/src/main.rs:80`). It is on by default only on Ubuntu Core. The
  generic capture only warns when a header can't be kept
  (`holt/src/luksheader.rs:245ff`); the refusal is Ubuntu Core's own,
  `snapcore::Dump::require_headers` (`holt/src/snapcore.rs:966-983`).
- **dump runs `cryptsetup luksHeaderBackup`** on the source
  (`holt/src/luksheader.rs:219`), so the image must ship the `cryptsetup`
  command (not listed, above; GN1 checks).
- **Secure Boot.** Only the `SecureBoot` and `SetupMode` bytes are recorded
  (`holt/src/efi.rs:331-332`). PK, KEK and db are not, and nothing here
  proposes that.
- **homed, sysext, confext, repart, sysupdate: nothing.** None is named
  in holt/src outside fixtures.

**`bko-holt load`:**

- **Format skips raw volumes** (`is_raw_volume`,
  `holt/src/load/commands.rs:3984`); the restore stage writes them by
  `bko-holt raw-write`, chunk by chunk (`write_diff`,
  `holt/src/load/rawvol.rs:454`). Both assume an LV target.
- **A new LUKS header by default.** When every source keyslot is a
  token's, the passphrase keyslot gets a benchmarked PBKDF and a
  `LuksPbkdf` note (`holt/src/load/commands.rs:2339`). The lost tokens are
  listed for re-enrolment in the plan's `tokens` (`tokens_json`,
  `holt/src/load/jsonout.rs:784`, `:1148`).
- **`keep_header` exists** (`luks_keep_header`,
  `holt/src/load/commands.rs:2517-2531`): luksHeaderRestore, open with
  `--key-file`, no luksFormat, no token change. LUKS1 and `--new-uuids`
  refused. Its `KeptHeaderKeyNeeded` problem names Ubuntu Core's tool,
  `bko-holt recovery-key-file` (`holt/src/load/commands.rs:2602`), which is
  wrong for GNOME OS (below).
- **The mount stage refuses GNOME OS.** It mounts what `storage.fstab`
  lists (`holt/src/load/commands.rs:5246-5251`). With no fstab, it reports
  "nothing in fstab or the ZFS datasets mounts /" (`:5458-5461`). ABRoot
  and Ubuntu Core each needed their own mount set, `abroot_mounts`
  (`:5434-5440`) and `snapcore_mounts` (`:5441-5447`); Solus needed its
  ESP mounted with no fstab line, `solus_esp_mount` (`:5448-5452`,
  `:7034-7059`).
- **The generic boot stage needs an fstab and a chroot.** It starts with
  "refuses to start unless T/etc/fstab is the restored system's"
  (`holt/src/load/commands.rs:6097`), and runs `chroot T sh -c '...'` for
  SELinux labels (`:6639-6649`). With `/usr` a raw partition, `T/usr` is
  empty, so there is no `sh` and the step fails. ABRoot, Ubuntu Core,
  ostree and transactional systems return early to their own arms
  (`:6079-6094`).
- **Restore completion is judged by an fstab.** `restored_marker` defaults
  to `etc/fstab` (`holt/src/other/mod.rs:328-345`), and `status` checks
  `etc/fstab` and `etc/os-release` (`holt/src/load/status.rs:378-385`).
  Without one, a finished restore reads "restore ran, but the disks don't
  show it done".
- **The shim arm** makes only NVRAM entries
  (`holt/src/load/commands.rs:6494-6506`). The plain arm would run
  `bootctl install` (`:6521-6531`) and put an unsigned systemd-boot in
  place. Both sit inside the generic boot stage, so neither serves
  GNOME OS as it is.
- **No UKI building** (`holt/src/load/commands.rs:7367-7372` refuses
  Gentoo's `uki_generator`). Nothing here needs one.
- **NVRAM entries** come from the dump's `boot.efi.entries`
  (`efi_entries`, `holt/src/load/commands.rs:8034`).

**BackShot1** (paths under `testvm/realworld/backshot1/`):

- `bs1-adopt`'s `pkg_install` guards ostree only (`bs1-adopt:166`) and
  otherwise says "no package manager I know" (`bs1-adopt:191`).
  `install_tools` checks sshd, python3 and util-linux (`bs1-adopt:646-664`),
  which ship. But `start_mdns` calls `pkg_install avahi-daemon ...` when
  avahi-daemon is absent (`bs1-adopt:705-708`), and avahi isn't in 51's
  deps.bst (above), so adopt would stop there unless GN10 skips mDNS on
  GNOME OS. efibootmgr's absence is covered by bs1-efi through efivarfs
  (the AerynOS precedent).
- **Every LUKS step needs the drive's key to open the root.** bs1-luks
  requires `keys/disk-passphrase` and stops unless it opens every LUKS
  volume (`bs1-luks:1350-1356`; `lk_drive`, `:915-936`). bs1-backup's
  preflight does the same (`unlock_check` → `luks_key_test`,
  `bs1-backup:124-129`; `bs1-common:909-915`). bs1-restore passes that file
  as `--key-file` for every volume (`bs1-restore:121-125`, `layout_keys`
  `bs1-common:555`), and with a kept header that key must open an existing
  keyslot. A TPM-plus-recovery-key install has no such key unless the
  drive's passphrase **is** the recovery key (written with `printf '%s'`,
  no newline), or adopt adds the drive's key as a passphrase keyslot.
- `bs1-luks`'s "own" test reads crypttab options or a key file
  (`bs1-luks:1488-1496`). The gpt-auto root has no crypttab line, so a
  TPM-only volume isn't "own". It falls to family A, attended (once the
  key check above passes).
- `unlock_how` has no TPM case (`bs1-common:707`). `unlock_mode` trusts an
  "own" volume without evidence and prints family A as typed
  (`bs1-common:870-905`). `backshot1 cycle` refuses an attended run
  (`backshot1:262-268`).
- `UPDATERS` lists ostree and ABRoot updaters only (`bs1-common:180`), and
  adding to it alone does nothing: the updater holds and checks in
  bs1-backup and bs1-verify run only under `is_ostree` or `is_abroot`
  (`bs1-backup:115-118`, `:315-325`, `:350-360`; `bs1-common:167`, `:172`).
- `bs1-backup`'s `unhinted_check` refuses any `from: null` backup source
  outside ostree and ABRoot: "bko-holt dump lists sources nothing
  mounts..." (`bs1-backup:274-283`). The raw usr images are exactly that.
- `REAL_FS` has no erofs (`bs1-common:82`), so the manifest skips `/usr`.
  It also never sees the B slot.
- `bs1-manifest`'s EXPECTED excuses `efi/*` and `var/lib/*` wholesale
  (`bs1-manifest:78`, `:87`). That is too lenient for UKIs and sysexts.
- `bs1-qemu.py` has no swtpm option; `DRIVE_FAMILIES` is S D E B Z G P
  (`bs1-qemu.py:180`).

## The design

### One tree convention: the physical btrfs root

The backup takes the **root btrfs filesystem as it is on disk**, not the
merged view. `/etc` is always an overlay of the base confext (in `/usr`)
whose upper is the root's own `/etc` (above). Backing up the merged `/etc`
would copy the base confext's files into the upper and change the system.
So dump lists the root in `storage.backup_sources` as a non-recursive bind
of `/` (as for ostree and ABRoot), and the raw partitions as images. The
upper's **whiteouts must survive** the backup and the restore (Vanilla's
whiteouts are the precedent); *check* in GN1 that the guest's upper holds
some, and plant one if not.

### Detection

GNOME OS is `ID=org.gnome.os` plus the markers of a sysupdate system:
- `/usr/lib/sysupdate.d/*.transfer` with `MatchPattern=gnomeos_usr_@v`
  (GBM:files/sysupdate/81-gnomeos-usr.transfer:14);
- `/proc/cmdline` has `usrhash=`, and `/usr` is a dm-verity device;
- the ESP has `EFI/Linux/gnomeos_*.efi` (the ESP mounted first if `/efi`
  is an idle automount).

A slot is empty when its label is `_empty` (sysupdate's) or
`gnomeos_usr_empty` / `gnomeos_usr_v_empty` (the installer's); detection
and redact lists take both.

The generic part (R11) is a `holt/src/sysupdate.rs` that reads the
transfers, the installed versions (`InstancesMax`, `ProtectVersion`), each
UKI by `read_uki` (`.osrel` IMAGE_VERSION, `.cmdline` usrhash, the
`+L-D` boot counter in the name) and which slot is booted. GNOME OS
specifics (the label scheme, the A/B partitions) sit on top. dump writes
it as `boot.sysupdate`.

### `dump`

1. The `SUPPORTED` row `("org.gnome.os", "51", "GNOME OS 51")`, appended
   (R1). Nightlies refused until a guest tests them.
2. **verity classification:** `dm_json` already gives a `CRYPT-VERITY-`
   device `crypt_type: VERITY` (`holt/src/report.rs:541-546`). The fix keys
   on that (a target `verity`, or `crypt` consumers that skip
   `crypt_type: VERITY`), with its data and hash devices.
3. **erofs as content:** a probe that names it, enough for detection.
4. **Raw partitions** (GN3, generic): any GPT partition whose type is a DPS
   usr, usr-verity or usr-verity-sig type, or whose content is not in
   `MADE` (`holt/src/qubes.rs:238`) and isn't mounted read-write, is a raw
   source. That takes in the verity hash partitions (`DM_verity_hash`,
   above) as well as the erofs ones. Its image goes in the backup's tree at
   `/.bko-holt/rawpart/<PARTUUID>.img`, not under `RAW_DIR`
   (`holt/src/qubes.rs:26`), whose `VG/LV.img` names (`:245-247`) a VG
   called `part` would collide with. PARTUUID, not the label, since labels
   change at each update. The active `/usr` is read from its partition
   device, not its mount.
5. **The empty B slot** (R6): its GPT entry is kept, its bytes are not.
   Nothing writes B at install and the first-boot repart doesn't discard
   (above), so on a used disk B holds stale old data, not zeros: no
   dedupe, and a privacy leak into the backup. sysupdate takes a slot
   labelled empty as free to overwrite (sysupdate.d(5)), so load writes
   zeros (or discards) instead. *Check* in GN1 what the guest's B holds.
6. **LUKS headers kept by default** on GNOME OS (R4), as on Ubuntu Core.
   A header dump couldn't keep is a dump refusal of GNOME OS's own: the
   generic capture only warns (`holt/src/luksheader.rs:245ff`), and the
   refusal is Ubuntu Core's code, `snapcore::Dump::require_headers`
   (`holt/src/snapcore.rs:966-983`).
7. **`boot.sysupdate`**: versions, slots, UKIs, the booted version, and
   whether a newer unblessed UKI (`+3-0`) is waiting.
8. **The ESP:** dump triggers or mounts the `/efi` automount before it
   lists mounts, so the vfat ESP is a backup source, and records that
   `/efi` had no fstab line.
9. **The loader named explicitly** (above): `boot.loader` systemd-boot
   via shim when the ESP has `EFI/org.gnome.os/shimx64.efi` on a GNOME OS
   system, whatever BootCurrent points at.
10. **v1 refusals at dump:** a sysupdate job running; a newer UKI not yet
    blessed; an active systemd-homed LUKS home (R7); two GNOME OS disks.
11. `redact.rs`: `root` is already in `GENERIC_LABELS`
    (`holt/src/redact.rs:242`, `:265`); the versioned `gnomeos_usr_<v>` and
    `gnomeos_usr_v_<v>` labels need a pattern, since `GENERIC_LABELS` is an
    exact match (`:1307-1308`; `release_label` beside it is the place,
    `:1310`), and versions like `51.1-66` must match it. The recovery
    key's modhex shape (eight groups of eight) is scrubbed, as Ubuntu
    Core's 40-digit keys (eight groups of five) are
    (`scrub_recovery_keys`, `holt/src/redact.rs:1743-1750`).

### Raw GPT partitions in `load` (generic)

- `format` skips a raw partition, as it skips a raw LV
  (`holt/src/load/commands.rs:3984`).
- The restore stage writes it by `bko-holt raw-write` onto the partition
  device. `rawvol.rs` gains a partition target; its chunk diff already
  works on any block device (`holt/src/load/rawvol.rs:454`).
- **Sizes are exact.** A plan whose raw partition differs in size from the
  dump is refused. Only the root may grow, as only ubuntu-data may on
  Ubuntu Core (docs/design/ubuntu-core.md:806).
- A raw image whose length or SHA-256 doesn't match the dump's record is a
  restore failure, not a warning.
- **Who puts the images in the backup.** dump only lists them (`from:
  null`, as for LVs, `holt/src/qubes.rs:380-397`). An online backup needs
  a staging step that copies each partition sparse into a directory
  bind-mounted at `/.bko-holt/rawpart` during the backup, as
  `ht1-offline.sh` does for Qubes' LVs (`testvm/holttest1d/ht1-offline.sh:347-360`,
  `:453-455`): in `ht1.sh` (GN3/GN7), which has no raw handling today, and
  in `bs1-backup` (GN10), whose `unhinted_check` gains a GNOME OS
  exception (`bs1-backup:274-283`). About 8.5 GiB of images when both
  slots hold a version (two usr at 4 GiB, two verity at 275 MiB; half
  that with B empty, R6), counted in the RAM and disk costs below.

### What GN3 built (raw GPT partitions, generic)

`holt/src/rawpart.rs`, with its load half in `holt/src/load/rawvol.rs`.
Nothing in it is GNOME OS's.

**The API GN2 calls.** dump marks raw partitions in `report::build_with` by
`rawpart::mark(sys, &mut disks, &mountinfo, &rawpart::rule_for(os_id))`,
once the disks' JSON is built and beside Qubes's raw volumes. GN2 changes
only `rule_for`, giving `org.gnome.os` its own rule:

```rust
pub struct Rule {
    pub dps_usr: bool,                          // DPS usr / usr-verity / usr-verity-sig types
    pub unmade: bool,                           // content not in rawpart::MADE (or none)
    pub empty_labels: &'static [&'static str],  // exact GPT names of an empty slot
    pub empty_prefixes: &'static [&'static str],// GPT names starting so
}
Rule::GENERIC = { dps_usr: true, unmade: false, empty_labels: &["_empty"], empty_prefixes: &[] }
// GNOME OS (GN2), e.g.:
Rule { unmade: true, empty_labels: &["_empty", "gnomeos_usr_empty", "gnomeos_usr_v_empty"], ..Rule::GENERIC }
```

A partition mounted read-write itself (by devno, not through a dm device
on it) is never raw, and neither is an MBR one. `Rule::decide(part_json, rw)
-> Option<Pick>` (`Pick::Image(why)` or `Pick::Empty(why)`) is public, and
`rawpart::mark_with(sys, disks, mounts, &dyn Fn(&Value, bool) ->
Option<Pick>)` takes any decision, should GN2 need one a `Rule` can't say
(by the verity hash partitions' blkid type, say). `Rule::GENERIC` is on for
every system. No corpus or golden dump has a DPS usr partition, so no plan
changed. `unmade` is off by default because a BIOS boot partition or
another system's partition holds such content too.

**dump** gives each picked partition `raw_partition` {`image`
(`/.bko-holt/rawpart/<partuuid>.img`, lower case; null for an empty slot),
`why`, `empty`, `size_bytes` (the GPT entry's), `sha256` (read off the
partition device by dump, a MiB at a time; null with a warning when it
can't read it, unprivileged say)}. A partition that isn't an empty slot is
also a `backup_sources` row: `fstype: "raw"`, `from: null`, `source:
/dev/disk/by-partuuid/<partuuid>`. An empty slot has no source: its GPT
entry stays in the table, and its old bytes are neither backed up nor kept
(R6). Redaction rewrites the image path's PARTUUID as it rewrites the
partition's.

**load:**
- `format` leaves a raw partition alone (`is_raw_volume` covers both).
- `--fit-largest` refuses when the largest partition is raw (new
  `fit_refused` reason `raw_partition`, in holt-json.md).
- `rawpart_problems` refuses a plan whose raw partition's `size_bytes`
  isn't its `raw_partition.size_bytes` (an edited layout), and one whose
  image has no SHA-256 in the dump.
- The main restore leaves `!/.bko-holt/rawpart/**` out.
- The restore stage then writes each image: `ARCHIVER dump
  SNAP:/.bko-holt/rawpart/<partuuid>.img | bko-holt raw-write --size N
  --sha256 H --chunk 1048576 /dev/<target partition>`. Only the chunks
  that differ are written. A short stream, or a SHA-256 that isn't the
  dump's, fails the stage. An empty slot gets `bko-holt raw-write --size 0
  ... < /dev/null`, which writes zeros wherever it isn't already zero.
- Without `--repo`, a note says to do the same by hand.

**Staging (the shared helper for GN7 and GN10):** `bko-holt rawpart-stage
[--dev-dir DIR] LAYOUT DIR`. It copies each non-empty raw partition of
the dump LAYOUT sparse (4 KiB zero blocks become holes) from
`/dev/disk/by-partuuid/<partuuid>` (or `--dev-dir`) to
`DIR/<partuuid>.img`. Each image is checked against the dump's size and
SHA-256; a partition that changed since the dump fails with exit 1. It
removes DIR's other `.img` files and prints "PARTUUID BYTES SHA256" for
each image. The caller does the rest:

```sh
bko-holt dump > system.json
bko-holt rawpart-stage system.json "$STAGE"
mkdir -p "$TREE/.bko-holt/rawpart" && mount --bind "$STAGE" "$TREE/.bko-holt/rawpart"
rustic-bko backup ... "$TREE"; umount "$TREE/.bko-holt/rawpart"; rm -rf "$STAGE"
```

In ht1.sh (GN7) the dump runs in the guest and the backup on the host,
from the mounted overlay. `--dev-dir` then points at the host's view of
the guest's partitions, or by-partuuid works as it is once udev has seen
the overlay's partitions.

**Tests:** `rawpart::tests` (rules, mark and stage with a changed or short
device, unprivileged), `bytes::sha256_tests::streaming_matches`,
`load::rawvol::tests::sha256_checked`,
`load::fit::tests::a_raw_largest_is_refused`, and the scenario
`scenarios::raw_gpt_partitions`. That scenario is a Debian on GPT with an
A/B DPS `/usr` (A's usr and usr-verity, B `_empty` holding stale bytes),
taken from dump through redaction to the plan's format and restore
stages, plus the exact-size and missing-SHA-256 refusals.

### `load` on GNOME OS

- **The table:** the generic sfdisk one, exact. Under `--fit-largest` only
  `root` grows. Whether PCR 5 (the GPT) is in the policy at all is
  UNCONFIRMED: no lock-gpt component is enabled (above), and pcrlock
  leaves out a PCR whose log it can't recognise. GN1's `systemd-pcrlock
  predict` settles whether a grown root needs the recovery key once; the
  plan's note says what GN1 finds.
- **The mounts** (GN4): load's mount stage reads `storage.fstab` only
  (`holt/src/load/commands.rs:5246`); GNOME OS has none, so GN4 adds a
  `sysupdate_mounts` set beside `snapcore_mounts` (`:5441-5447`), or the
  plan fails at `:5458`. It mounts the root by GPT type and label `root`
  (opened as `root`, its `VolumeName=`), and the ESP at `/efi` with no fstab
  line, as `solus_esp_mount` does for Solus (`:7034-7059`). `/usr` is not
  mounted; the restore writes no file under it.
- **LUKS:** `keep_header` (R4). The header is restored, then opened with
  the recovery key **as a passphrase**: the dashed modhex text exactly, in
  a key file with no newline (written so, GBM51 main.rs:429-437). The
  precedent is Aeon's (the recovery key text as `--key`/`--key-file`,
  docs/design/aeon.md:640-644), not Ubuntu Core's `bko-holt
  recovery-key-file`, which takes only eight groups of five digits and
  writes their 16 binary bytes (`holt/src/snapcore.rs:1007-1041`) and
  would refuse a modhex key. The `KeptHeaderKeyNeeded` hint
  (`holt/src/load/commands.rs:2602`) gains a GNOME OS wording. *Check* in
  GN1 that the key opens with `--key-file`. Option B (R4's alternative): a
  new header with the recovery key as its only passphrase, and the TPM2
  and recovery tokens listed for re-enrolment by `tokens_json`.
- **The boot stage regenerates nothing, in an arm of its own.** GNOME OS
  returns early to a `sysupdate_boot` arm, as `abroot_boot` and
  `snapcore_boot` do (`holt/src/load/commands.rs:6079-6094`): no fstab
  check, no `chroot` (there is no `/usr` in the target), no initramfs, no
  `bootctl install`. The ESP comes back file by file: shim, systemd-boot,
  the UKIs with their `+L-D` names, `.extra.d` addons, `loader/keys`,
  `loader/credentials` and `random-seed`. The arm writes load's log, runs
  a check script and makes the NVRAM entries by `efi_entries`
  (`holt/src/load/commands.rs:8034`), for shim at
  `\EFI\org.gnome.os\shimx64.efi`. (shim's fallback would also recreate
  that entry at the first boot from the disk, above.) The check script
  checks each UKI's `usrhash=` against the restored PARTUUIDs: per the DPS
  the data partition's UUID is the root hash's first 128 bits and the
  verity partition's its last 128 bits. Not with `veritysetup dump`: it
  reports the superblock's parameters, and the root hash isn't stored
  there (https://man7.org/linux/man-pages/man8/veritysetup.8.html, DUMP).
  A full `veritysetup verify DATA HASH ROOTHASH` reads all 4 GiB, and the
  stick doesn't promise veritysetup: it is not in `REQUIRED_COMMANDS`
  (`stick/slim-base-layer.sh:74-75`), and nothing in `stick/` names erofs
  or verity. `read_uki` takes the first `.cmdline` section
  (`holt/src/zfsbootmenu.rs:174`, `:184`), but the "Safe mode" profile
  and each `.extra.d` addon bring their own, so the check reads every
  profile's and addon's `.cmdline`. It also checks that the booted
  version's UKI exists and that no `bootctl install` ran. This is ABRoot's
  "the configs lead to things that exist" (docs/design/vanillaos.md:48-54).
- **Restore completion:** a `restored_marker` entry for GNOME OS
  (`holt/src/other/mod.rs:328`), and the same in `status`
  (`holt/src/load/status.rs:378-385`), e.g. the root's
  `var/lib/systemd/` or `etc/machine-id`, since there is no `etc/fstab`
  (*check* on GN1 which file every install has).
- **No initramfs step:** the generator is "prebuilt, in the UKI" (a null
  generator would run `update-initramfs`, above).
- **v1 plan problems:** `--new-uuids` (usrhash and PARTUUIDs are tied, and
  repart derives the LUKS2 UUID from the root's PARTUUID); a target
  smaller than the fixed partitions; a raw image missing.

### TPM2: what a restore promises

GNOME OS seals the root's key in **one** `systemd-tpm2` LUKS token
(GNOME's docs check `tpm2-pcrlock: true` on it,
https://os.gnome.org/docs/manual-dual-boot.html). Its policy combines the
UKI's signed PCR 11 policy (51's two PCR keys, no policyref, above) and,
where systemd-pcrlock works, a pcrlock policy: an automatically allocated
NV index, predicted up to component location 770, the initrd (GBM51
main.rs:212-255; systemd-pcrlock(8), `--location=`, `--nv-index=`).
Whether both must be satisfied is UNCONFIRMED. `/var/lib/systemd/pcrlock.json`
holds more than a copy: the prediction, the NV index's data and an
encrypted access PIN. A copy is also written as a credential to
`ESP/loader/credentials/`, and that copy is what the initrd uses (the
installer copies it there, GBM51 main.rs:241-251, 279).

1. **Same machine, kept header, same disk size:** unattended, if every
   PCR in the policy predicts the same values. PCR 4 and 11 need the same
   ESP bytes and UKIs, and the ESP's `loader/credentials` must come back.
   Whether PCR 5 (the GPT) and PCR 15 are in the policy at all is
   UNCONFIRMED: no lock-gpt component is enabled, the machine-id
   component (820) lies past location 770, and the file-system lock
   (830/840) is disabled (systemd-pcrlock(8);
   GBM51:files/gnomeos/systemd-presets/80-pcrlock.preset:9,28-29). PCRs 1
   and 3 (firmware config) are not locked by default. **UNCONFIRMED**:
   GN1 reads `systemd-pcrlock predict` and `pcrlock.json` for the real PCR
   set; GN7's same-TPM cell settles the outcome.
2. **Same machine after an update re-sealed:** the NV policy then names
   the newer PCRs, and the restored older UKI may not match. This is
   Ubuntu Core's cell f question (docs/design/ubuntu-core.md:415). For
   pcrlock it is UNCONFIRMED.
3. **New TPM, or a firmware or Secure Boot change:** the recovery key is
   typed once at the console. Then the user re-seals with GNOME's three
   commands, in 51's form (https://os.gnome.org/docs/using.html, "Re-enroll
   tpm2": "Version 51 and older should not use
   --tpm2-public-key-policyref=initrd"):
   `run0 /usr/lib/systemd/systemd-pcrlock remove-policy`,
   `run0 /usr/lib/systemd/systemd-pcrlock make-policy --location=770
   --pcr=0+1+2+3+4+5+7+11+14+15`,
   `run0 systemd-cryptenroll --wipe-slot=tpm2 --tpm2-device=auto
   /dev/disk/by-partlabel/root`. (The `--tpm2-public-key-policyref`
   options are "Added in version 262",
   https://www.freedesktop.org/software/systemd/man/latest/systemd-cryptenroll.html,
   and are master's.) The plan says so in a note. bko never re-seals or
   re-enrols (refused, as for Aeon and Ubuntu Core).

### Secure Boot keys

bko records only the SecureBoot and SetupMode flags (`holt/src/efi.rs:331`).
On the same machine nothing changes. On a new machine with GNOME's keys
wanted, the user puts the firmware in setup mode, and systemd-boot offers
the restored ESP's `loader/keys`. An installed ESP very likely has them:
the installer copies `/usr/share/factory/efi` (GBM51 main.rs:277), the
build's `/efi` with `loader/keys` (signed-boot-common.bst:17-36); the ISO
strips them only from its own ESP (GBM:elements/gnomeos/live-image.bst:61-62).
*Check* on GN1. In setup mode systemd-boot's default
`secure-boot-enroll=if-safe` enrolls "auto" by itself in a VM
(https://www.freedesktop.org/software/systemd/man/latest/loader.conf.html);
GNOME's docs call that set "default". Its PK, KEK and db are the `-mic`
files (signed-boot-common.bst:21-24), which add Microsoft's UEFI CA 2011
and 2023 to db, with no dbx (GBM51:elements/gnomeos/efi-keys.bst, per the
fact review; not re-read here, UNCONFIRMED). The docs say to run `run0
/usr/lib/systemd/systemd-pcrlock unlock-secureboot-policy` before
enrolling (https://os.gnome.org/docs/print.html); on 51 the
secureboot-policy lock isn't enabled (above), so that step unlocks
nothing there.

### systemd-homed

Not the default, so v1 refuses an **active** homed LUKS home (a loop-backed
dm-crypt device) at dump, with "log the user out, or `homectl deactivate`".
An inactive `~user.home` image is an opaque file and comes back as one,
with `/var/lib/systemd/home/` as files (*check* that a homed user logs in
after a restore, if R7's guest option is taken).

## Guests

**`gnomeos51`**: UEFI, Secure Boot **off** (vm-run `--uefi
--no-secure-boot`, `testvm/vm-run:21`), no TPM, so **unencrypted** (the
initrd's `disable-encryption.service`). 8 GiB RAM and 4 CPUs as upstream's
VM (GBM51:utils/run-secure-vm.sh:328,331), est. 50 GiB sparse disk
(upstream's size, run-secure-vm.sh:364,370). The ISO in
`/bkoscratch/rebuild-inputs/gnomeos51/`, fetched from its versioned URL
(`.../tag/51.1/gnome_os_51.1-x86_64.iso`, not the `stable/51` alias) and
pinned by our own SHA-256 since GNOME publishes none (Q2).

**`gnomeos51-tpm`**: the same plus swtpm, so LUKS2 + TPM2 + recovery key.
It uses the existing convention: `vm-run-options` with `--guest-vars
uefi-vars.fd --guest-tpm tpm` (`testvm/vm-run:141`; `testvm/vm-lib.sh:76-81`).
Secure Boot **on**, firmware vars started in setup mode, and systemd-boot
enrols GNOME's "auto" keys (with Microsoft's, so the rescue stick's shim
still boots; GBM docs/hardening.md:103-121) (R3). vm-run has no
setup-mode word, but an existing `uefi-vars.fd` copied from
`OVMF_VARS_4M.fd` is used as is (`testvm/vm-run`, `--guest-vars`). The
recovery key is a testbed secret in a 0600 file outside the repo, linked as
`recovery-key`, as `uc24-fde`'s (`testvm/make-ubuntu-core.sh`). After
enrolment, `uefi-vars.fd` holds GNOME's PK, KEK and db, so the guest's
restored boots need that store too (HoltTest1, below): a fresh
`OVMF_VARS_4M.ms.fd` holds only Microsoft's keys, and GNOME's shim is then
refused.

How it's built (R2):
1. **Upstream's raw pre-install** (recommended): `systemd-repart
   --image=<ISO> --definitions=<repart.raw.d> --empty=force` onto the
   guest disk (GBM51:utils/run-secure-vm.sh:363-365), then the first
   boot's initrd repart makes the root (encrypted on `-tpm`). This path
   gives a **1 GiB ESP** (GBM:utils/repart.raw.d/10-efi.conf), not the
   installer's 500M-1G, and a TPM2 slot **without** pcrlock until
   re-enrolled (https://os.gnome.org/docs/manual-dual-boot.html). So
   GN1 re-enrols the TPM2 slot with GNOME's three commands before the
   guest is published (else cell c tests a weaker seal than a real
   install's). Upstream passes `gnome.initial-setup=0` only with
   `--home-disk` (run-secure-vm.sh:382); that it skips the GUI here is
   UNCONFIRMED. The user, `authorized_keys` and an enabled sshd are
   written on the root once, by upstream's own recipe: `systemd.wants=sshd.service`
   and the `ssh.ephemeral-authorized_keys-all` credential through SMBIOS
   (run-secure-vm.sh:416-418, 472-485), then made persistent. systemd-stub
   reads `io.systemd.stub.kernel-cmdline-extra` from SMBIOS unless it is
   in a confidential VM, Secure Boot or not, and measures it into PCR 12
   (https://www.freedesktop.org/software/systemd/man/latest/systemd-stub.html,
   "SMBIOS Type 11 Strings"), which GNOME's pcrlock leaves out
   (GBM51:files/gnomeos/systemd-pcrlock-workaround.conf:1-2,7). On
   `-tpm`, a real recovery key is enrolled with `systemd-cryptenroll
   --recovery-key --wipe-slot password`, GNOME's documented way to replace
   `ihavenotsetarecoverykey` (https://os.gnome.org/docs/manual-dual-boot.html).
   Needs a host systemd-repart new enough for those definitions
   (UNCONFIRMED on trixie-backports); else it runs in a throwaway guest.
2. GNOME's installer daemon over D-Bus (`org.gnome.Installer1`, method
   `Install(device, oem_install)`, GBM51 main.rs:570-635) in the booted
   live ISO: the real installer's result, with the systemd-recovery token,
   the pcrlock policy and the installer's ESP. est. +0.5 d; headless use
   UNCONFIRMED.
3. One GUI install kept as a pinned image (not rebuildable).

**Both slots in use.** One `updatectl update` fills B and makes it the
booted version; A becomes the older. The feed always holds a newer build
than the ISO's (rolling 51.x, above), so the plain update works; to make
it repeatable, GN1 pins it, `updatectl update host@51.1-NN`
(https://www.freedesktop.org/software/systemd/man/latest/updatectl.html).

**Room** (est., *check* in GN1): ≈9.0-9.5 GiB of fixed partitions, ~3-4
GiB of root after install, so ~10-13 GiB a guest on disk, of which two usr
images are ~6-8 GiB. vanilla3's 30 GiB already needed `holt_disk_mode`
(testvm/holttest1d/README.md), which is a host setting
(`testvm/hosts.json:21`), set on chopin and cinqcent for it.

Each guest gets: `rebuild-one.sh` lines and its ISO check, the rebuild
inputs on Chopin and one more host, `ram-sizes.tsv` rows, every host
through `vm-store` publish (the `tpm/` dir goes as sidecar files, as for
`uc24-fde`), a GuestTest1 grade.

**Guest tools:**
- vm-mount: root by GPT label `root` (no fstab), LUKS by the
  `recovery-key` link, `/usr` read-only from the booted slot's verity
  (*check* the host's erofs and veritysetup).
- vm-update: a `# t2-section: Linux.sysupdate` in
  `testvm/update/in-guest.sh` (`updatectl update`, reboot, check the
  booted version), modelled on `Linux.abroot` (`:397`) and
  `Linux.snapcore` (`:478`). `gnomeos51-tpm` stays `--try`/`--probe`
  only, since vm-update can't merge a `--guest-tpm` guest
  (`testvm/vm-update:2617-2618`).
- Refresh by a GNOME-only offline tool like `testvm/sculpt-update` is
  **not** proposed: it would reimplement sysupdate's transfers (R10).
  A `--check` that compares `updatectl list host` with the feed's
  `SHA256SUMS` is worth having (51.x vs 52: a new guest).
- GuestTest2: a `pm sysupdate` value, a new kernel rule `K-uki` (UKIs in
  `ESP/EFI/Linux`, at most 2 by `InstancesMax`), like `K-abroot` and
  `K-snapcore` (`testvm/test2/t2lib/guest_judge.py:35`, `:61`), and a
  UKI-aware loader rule. Plain `L-sd` would fail: `l_sd` returns "no
  systemd-boot entries" unless `loader/entries/*.conf` exist
  (`testvm/test2/t2lib/guest_judge.py:717-721`; `check-image.sh:56-63`),
  and GNOME OS has only Type #2 UKIs in `EFI/Linux`. So `L-sd` learns UKIs,
  or a new `L-uki`. Also for GN5: the `Linux.sysupdate` name in
  `test_vm_update.py`'s section list (`testvm/test2/tests/test_vm_update.py:150-152`)
  and the new pm in `LINUX_PMS` (`testvm/test2/tests/fixtures/p2/world.py:92`).

## Tests

**SticTest1** (`testvm/stictest1/gnomeos51`): the root btrfs fixture as
for aeon. The verity `/usr` is a whole-disk-out-of-scope row in
`limits.json`, as `uc24`'s. Linux guests run alone in SticTest2
(`testvm/test2/classes.json:11`). Both archivers, two hosts, 0 FAIL.

**HoltTest1** (R8: **1Obscure**). Cells:

| Cell | Guest | Target TPM | Must show |
|---|---|---|---|
| a | `gnomeos51` | none | the restored default boot: same version, same usrhash, both usr slots byte-identical, root files equal |
| b | `gnomeos51` | none | the older slot booted by `bootctl set-oneshot`: equal to the source's older boot |
| c | `gnomeos51-tpm` | a copy of the source's | unattended unlock (no recovery key typed), the kept header's tokens present |
| d | `gnomeos51-tpm` | a new one (`R/restored.tpm`) | the recovery key typed by `console-boot.py`, then the three re-seal commands over SSH, then an unattended boot |

ht1.sh already keeps LUKS headers for a `--guest-tpm` guest
(`testvm/holttest1d/ht1.sh:1847-1856`) and boots restored guests on a new
TPM (`ht1.sh:371-377`). New in GN7:
- **cell c's TPM:** a copy of the source's TPM for the restored boot
  (Ubuntu Core's U6 needs it too);
- **the restored boots' NVRAM:** ht1.sh keeps a guest's variable store only
  for `--no-secure-boot` guests (`testvm/holttest1d/ht1.sh:353-363`); a
  Secure Boot guest's restored boots get a fresh `OVMF_VARS_4M.ms.fd`
  (`testvm/vm-run:535-537`, `:563-565`), Microsoft's keys only. GNOME's
  shim (signed by GNOME's key) is then refused on every restored boot, and
  PCR 7 changes: cells c and d would fail for a reason that isn't bko's.
  So cell c boots on a copy of the source's `uefi-vars.fd` (GNOME's
  enrolled db), and cell d, a new machine, boots on a fresh store in setup
  mode and enrols "auto" again (systemd-boot's `if-safe`, above; with
  Secure Boot off as the fallback if enrolment doesn't run unattended);
- **the raw images in the online backup:** a staging step (above), which
  ht1.sh lacks today.

judge.py **requires** byte-identical raw partitions (B's GPT entry only,
R6), UKIs and `loader/` files, and **allows** load's log and the boot
counter's rename.

**Test2:** a golden plan in `holt/tests/corpus/gnomeos51/` (`src.json`,
`plan.json`, `plan.txt`, as `holt/tests/corpus/uc24/` has; the `index.tsv`
row, the corpus README), `classes.json` (holt
family Obscure and `uefi`), holt/spec.json and guest/spec.json,
`estimates.tsv`, plan.py counts, world.py `GUESTS`, re-blessed p2 goldens,
in the sibling sessions' **one batched Test2 pass**. `gnomeos51-tpm` waits
for a HoltTest1 PASS dump, as `aeon` and `uc24-fde` do
(`testvm/test2/README.md:36-45`). Done when two corpus builds have the same
sha256, leak scans are 0 on both hosts and the Mac, no other plan changes,
and Test2's own tests pass.

**BackShot2:** a `sysupdate` trait module in
`testvm/backshot2/bs2lib/families/` (auto-collected), fixed partitions with
only root resizable, drift planted on the root's `/srv`. Later, with
KDE Linux's guest.

## BackShot1 on the A100

After the bs1 fixes (GN10) and a `bs1-qemu.py` rehearsal on `gnomeos51`
and `gnomeos51-tpm`:

1. **Install (attended, first).** The user installs GNOME OS 51 by hand
   from the live ISO with Secure Boot off (the install docs' path, R9). If
   the A100 has a usable TPM2, the install is encrypted and shows a
   recovery key; the user keeps it on paper and in the testbed secrets.
   At the console: `run0 systemctl enable --now sshd.service` and root's
   `authorized_keys`. Then everything is over SSH. Whether the A100 has a
   TPM2 is UNCONFIRMED: Aeon ran there in passphrase "fallback mode"
   ([[aeon-backshot1]]), which needs no TPM (Q4).
   A scripted install from the stick (`gnomeos-bootstrap.sh`: repart with
   CopyBlocks from the ISO, as the manual dual-boot recipe,
   https://os.gnome.org/docs/manual-dual-boot.html) is possible but not
   proposed: it needs systemd-repart on the stick, which isn't in its
   `REQUIRED_COMMANDS` (`stick/slim-base-layer.sh:74`), and it gives the
   literal recovery key, not the installer's (Q6).
2. **Adopt with no package manager.** `bs1-adopt` gets a GNOME OS guard
   beside the ostree one (`bs1-adopt:166`): nothing is installed; a missing
   tool is "not on GNOME OS" (or, for build tools, `updatectl enable devel
   --now`, which BackShot1 doesn't need). That covers `start_mdns`'s
   avahi install (`bs1-adopt:705-708`): on GNOME OS, mDNS is skipped if
   avahi isn't there and the A100 is reached by address. `cryptsetup` must
   ship (dump, bs1-luks and `luks_key_test` all run it; GN1 checks).
   `/etc` is writable, so the sleep drop-in, hostname, sshd drop-in, host
   key and firewalld change work. *Check* that `/usr/local` is writable
   before families D or P would need it (they won't here).
3. **Unlock.** Two cases:
   - **No TPM2 (or the TPM off in firmware):** no LUKS, `UNLOCK=none`. The
     simplest first run.
   - **TPM2, the drive's key first.** bs1-luks (`bs1-luks:1350-1356`),
     `unlock_check` (`bs1-backup:124-129`) and bs1-restore
     (`bs1-restore:121-125`) all need the drive's key to open the root
     (above). GN10 has the user store the recovery key as the drive's
     `keys/disk-passphrase` (`printf '%s'`, no newline), recommended (R12,
     Q11): nothing on the machine changes. The alternative is adopt adding
     the drive's key as a passphrase keyslot, which the kept header then
     carries back.
   - **Then "own via TPM token"** in bs1-luks, which replaces only the
     family/own decision: read the LUKS2 header's tokens (`cryptsetup
     luksDump --dump-json-metadata`) and call a volume with a
     `systemd-tpm2` token "own", even with no crypttab line
     (`bs1-luks:1488-1496`). No new drive family, though not because the
     initrd couldn't read the drive (usb-storage and ext4 are very likely
     built in, above): GNOME OS has no crypttab for a key-file line, its
     initrd is inside the signed UKI, so bko can't add a key path to it
     without re-signing (refused), and a key on the ESP protects nothing
     (docs/design/bs1-luks-unattended.md). **The TPM makes it hands-free
     natively**, if keep_header holds.
   - **Evidence.** `unlock_mode` today trusts "own" with no evidence
     (`bs1-common:870-905`). A TPM case in `unlock_how`
     (`bs1-common:707`) reads this boot's journal for systemd-cryptsetup's
     token-unlock line, and calls the boot "typed" if it shows a
     password prompt or a recovery-key unlock. The exact messages are
     UNCONFIRMED; GN10 takes them from the guest's journal.
   - If the restored boot asks for the recovery key, the run is attended:
     it goes first in any series ([[front-load-manual-tests]]).
4. **Updates held.** The user keeps automatic updates off in GNOME
   Software for the whole run (whether it downloads by itself is
   UNCONFIRMED) and the timers stay disabled (their preset). **Firmware
   updates too:** GNOME Software is built with `-Dfwupd=true`
   (GBM:elements/core/gnome-software.bst:45-52), and a firmware or dbx
   update changes PCRs (https://os.gnome.org/docs/using.html), so the hold
   covers fwupd as well. `UPDATERS` gains `systemd-sysupdate-update`,
   `systemd-sysupdate-reboot` (the units GNOME's preset names,
   GBM51:files/gnomeos/systemd-presets/50-sysupdate.preset:1-2),
   `systemd-sysupdated` and `fwupd` (`bs1-common:180`), and a new
   `is_sysupdate` predicate beside `is_ostree` and `is_abroot`
   (`bs1-common:167`, `:172`) opens the gates the holds run under
   (`bs1-backup:115-118`, `:315-325`, `:350-360`); without it the new
   names do nothing. A new `bs1-sysupdate`, modelled on `bs1-ostree` and
   `bs1-abroot`: preflight refuses a running sysupdate job, a newer
   unblessed UKI, or a booted version older than the newest installed;
   record writes both slots' labels and UUIDs, the UKIs with counters,
   bootctl's default and the LoaderEntry variables.
5. **Dump and backup:** the root files plus the raw usr images (GN3),
   staged by bs1-backup into the directory at `/.bko-holt/rawpart`, with
   `unhinted_check`'s GNOME OS exception (`bs1-backup:274-283`).
6. **To the stick, restore, boot.** The NVRAM entry comes back by the
   GNOME OS boot arm (`efi_entries`); if it didn't, shim's fallback
   (`EFI/BOOT/fbx64.efi`, restored with the ESP) would recreate it at the
   first boot from the disk (https://os.gnome.org/docs/manual-dual-boot.html).
   bs1-to-stick falls back to bs1-efi (efivarfs) without efibootmgr.
7. **Verify.** `package_list` gives IMAGE_VERSION, `updatectl list host`,
   `updatectl features` (devel, codecs-extra, nvidia-driver, snapd+apparmor,
   debug, as enabled), `systemd-sysext list`, `systemd-confext list` and
   the sysext and confext images' hashes instead of "no package manager
   known" (`bs1-common`). The manifest gets: each usr and
   usr-verity partition by label, type, PARTUUID and raw SHA-256, required
   identical; a KEPT-with-rename rule for `EFI/Linux/gnomeos_*.efi` (the
   `+L-D` counter stripped, bytes kept), as rEFInd's and Solus's KEPT
   rules; sysext images in `/var/lib/extensions` byte-identical, though
   `var/lib/*` is excused (`bs1-manifest:87`); a `gnomeos_paths` list of
   expected churn measured on a two-boot diff, as B1 and B9 did
   (pcrlock files, `random-seed`, uresourced's drop-ins).
8. **The older slot's boot** by `bootctl set-oneshot` over SSH. That
   needs no console, unlike ostree's and ABRoot's GRUB menu
   (`backshot1:268`).

Known, not fixed here: a firmware update on the A100 between backup and
restore breaks TPM unlock with or without bko (https://os.gnome.org/docs/using.html).

## Shared lines (with the other new-distro sessions)

- **KDE Linux** (a sibling session's docs/design/kde-linux.md, an
  uncommitted draft written the same day; its "Shared lines" names the
  same built-once pieces). Shared: systemd-sysupdate with url-file
  transfers and `ProtectVersion=%A`, a versioned UKI in `/EFI/Linux` with
  boot counting, systemd-boot, erofs `/usr`, a btrfs read-write root,
  factory `/etc`, sysexts, Flatpak, no package manager, run0, Secure Boot
  off at install. **Not shared:** KDE Linux keeps `/usr` as erofs **files**
  on the btrfs root (`/system/kde-linux_<v>.erofs`,
  KL:mkosi.extra/usr/lib/systemd/system-generators/kde-linux-mount-generator:81-83),
  has no dm-verity, keeps 4 versions not 2 (KL:.../50-root-x86-64-erofs.transfer:19),
  TriesLeft 2 not 3, its own bless unit, LUKS2 as an optional Calamares
  choice with a passphrase and no TPM
  (KL:mkosi.extra/live/usr/calamares/desktop/modules/partition.conf,
  `luksGeneration: luks2`), and `/etc` as "etc-mstack"
  (KL:mkosi.extra/usr/lib/systemd/system/kde-linux-etc-mstack.service:5-29)
  with snapper configured for `/home`, not `/etc`
  (KL:mkosi.extra/usr/lib/kde-linux-snapper.conf,
  `kde-linux-snapper-home-config@.service`; per the fact review). Verity is
  still only a proposal (https://community.kde.org/KDE_Linux/Verity), and
  Secure Boot must be off (http://linux.kde.org/docs/install/). So KDE
  Linux needs **no raw partitions**, but the same detection, UKI
  inventory, boot check and tools.
- **Built once (R11):** `holt/src/sysupdate.rs` (transfers, versions,
  UKIs by `read_uki` with every profile's and addon's `.cmdline`, boot
  counters); the early-return sysupdate boot arm, its mount set and
  restore marker, and the ESP-kept boot check; the `Linux.sysupdate`
  vm-update section; `K-uki`, the UKI-aware L rule and `pm sysupdate` in
  GuestTest2; `bs1-sysupdate`, `is_sysupdate` and the `UPDATERS` lines;
  the no-package-manager guard in bs1-adopt (avahi included); the
  KEPT-with-rename UKI rule in bs1-manifest; the `sysupdate` BackShot2
  trait. Whichever session lands first builds them; the other rebases. A
  note goes into the shared folder.
- `osrel.rs` `SUPPORTED`: append; the second session to land rebases.
- `redact.rs` lists, `classes.json`, the holttest1d README row and
  check-manifests.py's count: one session at a time.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| GN1 | Fetch the 51.1 ISO from its versioned URL into rebuild-inputs (the user's OK first), pin our SHA-256. `make-gnomeos.sh` by R2's path, both guests on Chopin, `-tpm` re-enrolled with pcrlock, the one update into B pinned as `host@51.1-NN`, copied to a second host (same sha256), `rebuild-one.sh` lines. Settle every *check*: os-release (VERSION_ID, IMAGE_VERSION), the partition numbers, the verity hash partitions' blkid type, the `/etc` upper's whiteouts, the pcrlock PCR set (`systemd-pcrlock predict`, `pcrlock.json`), the recovery key as `--key-file`, the B slot's content, `loader/keys` on the ESP, which NVRAM entry the guest boots (fbx64's?), the `/efi` automount's idle timeout, `cryptsetup` and avahi in the image, `gnome.initial-setup=0`, sshd by SMBIOS credentials under Secure Boot, a restore marker file, room. Write "What GN1 found" here. Done when both boot alone to SSH, `-tpm` unattended twice, and both slots boot | 1.5 d |
| GN2 | dump (High subagent): the `SUPPORTED` row and test; `sysupdate.rs` (generic, R11) and the GNOME OS part; `boot.sysupdate`; verity by `crypt_type` in `dm_json`; erofs detection; the prebuilt-in-UKI initramfs; `backup_sources` (the physical root); the `/efi` automount triggered so the ESP is a source; the loader named explicitly (shim at `EFI/org.gnome.os`), not from BootCurrent; LUKS headers kept by default with a GNOME OS `require_headers` refusal; the v1 refusals; `redact.rs` (a pattern for versioned labels, `_empty`, the modhex key); a scenario fake tree; unit tests | 1.5 d |
| GN3 | Raw GPT partitions (generic, High): dump's raw sources for partitions (usr, usr-verity and verity hash) under `/.bko-holt/rawpart/`, `rawvol.rs` partition targets, format's skip, exact sizes, the image checks, an empty slot's GPT entry kept and zeros written (R6); the online staging step's shared helper (used by ht1.sh in GN7 and bs1-backup in GN10); scenario and unit tests | 1.25 d |
| GN4 | load (High): the `sysupdate_mounts` set (root by type and label, ESP at `/efi`, no `/usr`); the early-return `sysupdate_boot` arm (no fstab check, no chroot, the log, `efi_entries`) and its check script (usrhash against PARTUUIDs, every profile and addon); a `restored_marker` entry and `status`; the GNOME OS `KeptHeaderKeyNeeded` hint (recovery key as text); the TPM note and 51's re-seal commands in the plan; the v1 plan problems; `--fit-largest` on root only with GN1's PCR 5 finding; tests | 1.25 d |
| GN5 | Guest tools: vm-mount's GNOME OS mode, the `Linux.sysupdate` section (and its name in `test_vm_update.py`), fingerprint/pkgset, `K-uki`, the UKI-aware L rule, the pm in `LINUX_PMS`, compare.py's raw partition compare; tests | 1 d |
| GN6 | SticTest1 `gnomeos51`, both archivers, two hosts, 0 FAIL | 0.4 d |
| GN7 | HoltTest1 1Obscure: cells a-d, the same-TPM restored boot in ht1.sh, restored Secure Boot boots on the source's `uefi-vars.fd` (cell c) and a fresh setup-mode store with "auto" re-enrolled (cell d), the raw staging step in ht1.sh, the re-seal step, judge.py's rules, `ram-sizes.tsv`; runs on two hosts | 1.25 d |
| GN8 | Golden plan and the corpus test (`src.json`, `plan.json`, `plan.txt`); Test2 registration in the batched pass | 0.4 d |
| GN9 | Docs: holt/README.md's list, layload.md (raw partitions, the sysupdate boot stage), HoltTest1's row, SticTest1's README, memory | 0.2 d |
| GN10 | BackShot1 fixes: the adopt guard (avahi skipped), the drive's key (the recovery key as `keys/disk-passphrase`, R12), "own via TPM token", the TPM evidence, `is_sysupdate` and its gates, `bs1-sysupdate`, `UPDATERS` (fwupd too), the raw staging step and `unhinted_check`'s exception in bs1-backup, the manifest rules, `package_list`; `bs1-qemu.py` swtpm and vars pass-through (from vm-run's `--guest-tpm`), the new own case; rehearsal on both guests, both PASS | 1.5 d |
| GN11 | BackShot1 on the A100 (R9): the user's install, adopt, the cycle, verify, the older-slot boot | 0.75 d + the user's install |
| | *≈ 11 d in all; ≈ 8.75 d without GN10-GN11; +0.5 d on R2's path 2* | |

GN2 and GN3 can start beside GN1 from these facts; their golden plan (GN8)
waits for GN1's dumps and a HoltTest1 PASS dump. GN4 builds on GN3's raw
partitions, GN7 needs GN4's boot arm, GN5's compare.py and GN3's staging
helper, and GN10 waits for GN7. Each piece is committed on main
when its tests pass and the user says to commit; nothing is pushed until
the user says so. Source-code changes go to High-effort subagents
([[effort-max-when-it-pays]]). Every build and test job goes through
`testvm/pick-host` ([[split-chopin-cinqcent]]).

**Costs** (est.): GN1 ~1 h a guest (the ISO is 3.7 GB, the update pulls
~3.2 GB), `--reserve 30 4` in /mnt/bko-ram, moved to disk when done. Each
guest is ~10-13 GiB a host, plus the ISO. A backup carries ~8.5 GiB of raw
images (two usr, two verity), mostly deduped between runs, and the online
staging step holds the same ~8.5 GiB in the job's RAM reservation (or on
disk under `holt_disk_mode`) while the backup runs. GN6 and GN7 about 2 h
of queue a host.
GN11 needs the user at the A100 for the install (~30 min) and, if the TPM
cell is attended, for one recovery-key boot.

## Risks

- **pcrlock moves.** If the restored same-TPM boot doesn't unseal (cell c),
  the promise drops to "the recovery key once, then re-seal" everywhere.
  That is Aeon's promise, and the code is the same; only the docs change.
- **The empty-disk installer.** `--empty=require` (GBM51 main.rs:408-412, 459) means
  GNOME's installer never repairs or reuses a layout, so a bad restore
  can't be "fixed" by reinstalling over it without wiping. Load's plan
  problems must catch bad layouts first.
- **First-boot repart.** systemd-repart runs in the initrd on every boot
  (GBM:files/gnomeos/generate-initramfs/modules/50-repart/gnomeos.conf:11-13).
  A restored layout missing `root` or a B slot could make it create or grow
  partitions. GN1 tests a restore without B; the dump refuses one.
- **No published ISO checksum.** We pin our own. A silent re-upload under
  the same name would show as a pin mismatch, not as a bad guest.
- **Forward compatibility.** GNOME 52 may change the layout, and master
  already differs in the TPM seal (policyref, more locks). Each major is a
  new row and guest; the dump refuses an unknown major. The release post
  doesn't promise updates in perpetuity, and a reinstall may be needed later
  (https://blogs.gnome.org/adrianvovk/2026/10/16/gnome-os-preview).
- **Size.** Two full `/usr` images per guest and per backup. If the RAM
  disk can't take them, the line runs on a host with `holt_disk_mode`
  (a host setting, `testvm/hosts.json:21`; on chopin and cinqcent, as for
  vanilla3).
- **The CDN.** The feeds live on `1270333429.rsc.cdn77.org`. The feed
  always holds a newer build than the ISO's, so filling B works; a
  pinned `host@51.1-NN` fails if that build is dropped, and GN1 then pins
  the newest. Whether old builds are ever removed is UNCONFIRMED.
- **swtpm drift.** swtpm's version isn't a host fact that keys cells
  (testvm/test2 `facts_host.py`), so an upgrade changing the state format
  wouldn't re-run cells. Same risk as `uc24-fde` and aeon.
- **The literal recovery password.** A raw-image install's only key may be
  `ihavenotsetarecoverykey` (gen-recovery-key.sh:11). The guest builder
  replaces it; on real machines the plan warns if a key slot opens with it.
- **Old data in B.** On a used disk the empty B slot holds whatever was
  there (above). R6's recommendation keeps it out of the backup; R6's
  alternative would copy it.

## Out of scope

- GNOME OS nightly, GNOME OS 50 and older (refused in v1; later, if asked).
- arm64 GNOME OS.
- Dual boot, and any GPT with other systems' partitions (refused in v1).
- An active systemd-homed LUKS home (refused in v1, R7).
- TPM PIN, FIDO2 or PKCS#11 tokens added by the user: they come back with
  the kept header but aren't tested.
- **Refused by name:** bko re-sealing, re-enrolling or predicting PCRs
  itself; bko enrolling Secure Boot keys; rebuilding or re-signing a UKI;
  re-fetching `/usr` from GNOME's CDN instead of backing it up;
  `--new-uuids` on GNOME OS; a target with a smaller fixed-partition area.
- Custom or third-party sysexts beyond keeping their files. GNOME's own
  optional features (codecs-extra, nvidia-driver, snapd+apparmor, debug,
  devel) come back as files too and are listed by `package_list`, but no
  guest enables them in v1.
- The OEM install mode (GBM51 main.rs:466-467, `--defer-partitions=root`).

## Open questions for the user

1. **Q1.** Is GNOME OS on the A100 wanted at all now, or the guests only
   first (as Vanilla and Ubuntu Core were)?
2. **Q2.** GNOME publishes no ISO checksum. Is pinning our own SHA-256 of
   the first download enough, or should GN1 also check the feed's signed
   `SHA256SUMS` for the usr and UKI files inside the ISO? (That check
   likely fails: the ISO's build is probably plain `51.1`, and the feed
   has no plain `51.1` usr or UKI, only `51.1-NN`; UNCONFIRMED.)
3. **Q3.** May GN1 download the 3.7 GB ISO and ~3.2 GB of updates onto
   Chopin (and copy them to one more host)?
4. **Q4.** Does the A100 have a TPM2 (Intel PTT or a dTPM) and is it on?
   Aeon ran there in passphrase mode. The exact test is GNOME's own,
   `systemd-analyze has-tpm2` from the live ISO (GBM51 main.rs:648-656).
   Is it fine to run it, and to look in the firmware setup, at the install?
5. **Q5.** For the A100's first run: TPM on (the real default, a
   possible recovery-key boot) or the TPM off in firmware (no LUKS,
   hands-free for certain)?
6. **Q6.** Install the A100 by hand from the live ISO (R9), or should a
   `gnomeos-bootstrap.sh` from the stick be built (+0.75 d, needs
   systemd-repart on the stick)?
7. **Q7.** Is turning off GNOME Software's automatic updates on the A100
   for the whole run your step, as Bazzite's uupd was?
8. **Q8.** Should KDE Linux's guest and GNOME OS's share a new HoltTest1
   group, or both go into 1Obscure?
9. **Q9.** Is a homed test wanted (an extra `gnomeos51-homed` guest), or is
   the v1 refusal enough since homed isn't GNOME OS's default?
10. **Q10.** Should GNOME OS join the `+img` panel? It is not an
    oldest-target system, so this spec says no.
11. **Q11.** With the TPM on, every BackShot1 LUKS step needs a key on
    mybig512 that opens the root. May the A100's GNOME OS recovery key be
    stored there as `keys/disk-passphrase` (R12), or should adopt add the
    drive's passphrase as an extra keyslot on the A100 instead?

## Decisions for the user

**Approved by the user 2026-11-27 ("Approve as recommended"): every recommendation R1..R12, and each open question takes its recommended answer. Nothing is built until the user says to proceed.**

1. **R1. Which release:** **GNOME OS 51 stable** (`org.gnome.os` `51`,
   guests `gnomeos51` and `gnomeos51-tpm`) (recommended: the first
   "Preview", a published feed, a fixed major); or the nightly (moves
   daily, no stable row); or 51 and 50 (+0.75 d).
2. **R2. How the guests are built:** **upstream's raw pre-install with
   systemd-repart, then a real recovery key and a pcrlock re-enrolment**
   (recommended: GNOME's own VM path, scriptable, no GUI; it gives a 1 GiB
   ESP and, until re-enrolled, a TPM slot without pcrlock); or GNOME's
   installer daemon over D-Bus in the live ISO (+0.5 d, the real
   installer's ESP, tokens and pcrlock); or one GUI install kept as a
   pinned image.
3. **R3. Secure Boot:** **off on `gnomeos51`, on with GNOME's "auto"
   keys on `gnomeos51-tpm`** (recommended: the install docs' default and
   GNOME's hardened path; "auto" keeps Microsoft's UEFI CAs in db for the
   stick, per the fact review; restored boots on the source's NVRAM copy,
   GN7); or off on both (simpler, PCR 7 then tests nothing).
4. **R4. What an FDE restore promises:** **keep the LUKS2 header**
   (recommended: same machine unattended if pcrlock agrees; a new machine
   asks for the recovery key once, then 51's documented re-seal; Aeon's
   HoltTest1 keeps its headers the same way, `testvm/holttest1d/ht1.sh:1848-1856`,
   docs/design/aeon.md:993-1002); or a new header with the recovery key as
   passphrase and the tokens listed for re-enrolment (what load does
   without `--keep-luks-header`: always one typed boot, then re-enrol).
5. **R5. The /usr slots:** **raw partition images in the backup**
   (recommended: faithful and offline); or re-fetch them from the feed by
   version at restore (smaller backups; needs the network and the version
   still served; refused by name above).
6. **R6. The empty B slot:** **only its GPT entry kept, zeros written**
   (recommended, changed after the fact review: an empty-labelled slot is
   free space to sysupdate, and its bytes may be stale old data, not
   zeros, so copying it neither dedupes nor keeps anything the system
   uses, and leaks old data into the backup); or copied raw like the
   others (one rule, byte-identical disks, +~4.3 GiB a backup).
7. **R7. systemd-homed:** **refuse an active homed home in v1; an inactive
   image is a file** (recommended: not the default); or full support with
   a homed guest (+0.75 d).
8. **R8. HoltTest1's group:** **1Obscure** (recommended: beside aeon and
   vanilla3, no new group); or a new **1Sysupdate** with KDE Linux's guest
   (Q8).
9. **R9. BackShot1 on the A100:** **yes, GN11, installed by hand from the
   live ISO, front-loaded** (recommended: the only real TPM and real
   firmware test, and the user's own install path); or not now (-2.25 d).
10. **R10. Guest refresh:** **vm-update with a `Linux.sysupdate` section,
    `-tpm` by `--try`/`--probe` or a rebuild** (recommended: sysupdate
    does the work); or a `gnomeos-update` like `sculpt-update` (reimplements
    sysupdate's transfers, +1 d).
11. **R11. Shared pieces with KDE Linux:** **build the generic `sysupdate`
    module and tools once, in whichever session lands first**
    (recommended: about 1.5 d saved across the two); or each session its own.
12. **R12. The TPM evidence in BackShot1:** **the recovery key stored on
    mybig512 as `keys/disk-passphrase` for the key checks, then "own via
    TPM token" read from the LUKS2 header, proven by the boot's journal**
    (recommended: nothing on the A100 changes, no new family, TPM unlock is
    native; Q11); or adopt adds the drive's key as a passphrase keyslot;
    or treat every TPM boot as attended (the key checks still need a key;
    never hands-free).
