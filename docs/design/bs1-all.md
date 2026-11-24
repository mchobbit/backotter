# BackShot1All: every distro with a passing backup on mybig512, again, with today's tools (draft)

2026-11-24: widened at the user's word to every distro with a passing A100
BackShot1 and a backup on mybig512. The Dropped rows rule (a distro rejoins
once a fresh install passes BackShot1 on the Thinkpad) has let 30 runs back
in since 2026-11-17: rows 8-37 below, each named by its run on the drive,
which was listed read-only on 2026-11-24 (`runs/`, 289G free of 477G). Some
distros have two rows, one per stack (NixOS, Alpine, Artix, Manjaro,
Tumbleweed), since each is a different layout to restore. Three rows are
attended (typed passphrases: Ubuntu 26.10's ZFS keystore, Manjaro's GRUB
cryptodisk, Alpine's mkinitfs LUKS), so the attended block is back and goes
first (Order). The 2026-11-24 listing also showed that the seven rows of
the 2026-11-17 cut are all X13 backups (disk 214532A60C34), which A would
now restore onto the A100: Q6. Estimated machine time is now about 16 h 35 min, so the run spans
sittings (Order). The questions this opens are Q6-Q10 (Open questions,
2026-11-24); Decisions 1-5 still hold.

2026-11-17: cut to the seven distros with backups on the rebuilt mybig512.
The drive was wiped and rebuilt on 2026-11-16 (XFS, label mybig512), and
every library run of the old table went with it. Its seven passing runs of
2026-11-16 are the new table; the other distros wait for a reinstall
(Dropped rows). The attended block, the Bazzite rollback and the builds are
gone, and A and B reuse `backshot1 cycle` (Q3). The open questions are now 5,
and still need their answers before it runs.

2026-11-13: first drafted and stored at the user's request; revised the same
day after a review against the repo. Not yet run.

est. marks an estimate. L is a distro's library run (its backup on mybig512),
B its fresh run.

## Goal

Show that today's `bko-holt` and `rustic-bko` do a full bare-metal round
trip on the Thinkpad (the A100) for every row of the table, each a distro
with a passing backup on mybig512, from that backup and from a fresh one,
without reinstalling any of them. Today's tools are the ones staged under
`backshot1/bin` on the drive (read 2026-11-24):
- `bko-holt` 0.2.6, static-pie, from `~/backotter-work/holt-bin/linux/` on
  Chopin: sha256 86152cb6…, staged 2026-11-23 11:56 (the Artix 5e4ee13
  build; eight older builds sit beside it: `bko-holt.0.2.6-old`,
  `bko-holt.pre-artix` and six `bko-holt.prev-*`);
- `rustic-bko` 0.11.4+backotter.16, static: sha256 f41a91c7…. ForkAudit1's
  rel14 is built but not promoted; if it is promoted before the run, it is
  staged first and this line changes.

Main's scripts include 043ea1a (LUKS family N, NixOS) and fe9ef31 (adopt
installs efibootmgr and loads xfs on Alpine; DRIVE loads xfs before
mounting). 60ce2b8, docs only, records family N's Thinkpad proof and is the
one unpushed commit on 2026-11-17.

## Approach: no reinstalls, two halves per distro

- **A, restore the library backup with today's tools:** `backshot1 cycle L
  1 --erase-serial S7XMNF0Y787118 --hold` from the installed system that is
  running (Q3). That is `to-stick`, `plan`, `phase2 L`, `reboot`,
  `wait-host`, `bs1-hold on` (Tooling 3, 6), `verify L`. This tests:
  - load, including the zeroing of disk ends, since bs1-restore always
    passes `--erase`;
  - rustic-bko restoring the snapshot;
  - the boot stage;
  - all of it against L's own baseline.

  Every L has BackShot1's keys and hostname, so nothing is installed. Its
  snapshot is `latest:/mnt/bs1-src`.
- **The library spans 2026-11-16 to 2026-11-23.** Every L logged
  rustic-bko's staged sha256, but only two (Aeon's and Artix dinit's) logged
  the staged bko-holt's (the list under the table). So for the other 35 rows
  A is also a test of today's bko-holt loading an older build's dump, and
  the seven rows of the 2026-11-17 cut are X13 backups loaded onto the A100
  (Q6).
  Item 0 still compares each L's `run.log` sha256 lines with the staged ones,
  in case a binary changes before the run.
- **B, a fresh round trip of the system A left,** in the same boot as A's
  verify: `preflight`, `phase1` (today's dump and backup), then `backshot1
  cycle B 1 --erase-serial S7XMNF0Y787118 --hold --load-wipes` (Q2, Q3), then
  the distro's extra checks. This adds dump, the backup, and load's own wipe
  on a disk that already holds the same layout, which is the b3af94d case:
  load zeroing the ends and writing a table that matches the one there. It
  doesn't test holder release: the rescue opens no LUKS volume (bs1-restore
  gives load the key only through `--key-file`) and imports no pool (the
  stick masks zfs-import-cache, zfs-import-scan and zfs-mount). Only Rocky
  (row 28, VG rlm on a plain partition) has LVM outside LUKS, so it is the
  one row where the stick may activate a VG and load release it; none has
  md. Holder release otherwise stays with the bs2 leftovers set, which
  seeds its own holders. A
  library backup of a restored system is how the 2026-11-09 five-wipe cycle
  ran (Ubuntu, Fedora), so the pattern is proven.
- **B needs A.** B backs up the system A restored, so a distro whose A can't
  pass has no B (Fix policy). Decision 1 made NixOS's row full A+B too.
- **Runs are always named in full,** never by distro: `bs1-list --resolve`
  takes the newest backup, which isn't always the one that passed: each B
  adds a newer run of its distro, and the drive also holds excluded runs
  (below). The driver takes B's name
  from phase 1's `RUN=` line and keeps it in its state.
- **No OS updates, and no re-adopt** unless preflight or the driver's own
  check names a missing tool. On CachyOS, bs1-adopt's `pacman -Sy` would be
  a partial upgrade, so there I ask first.
- **The holds last only for the boot.** `bs1-hold on` runs as soon as SSH
  answers after each boot of the installed system, before `verify`, and so
  also before B's phase 1, which runs in that boot. It:
  - starts `systemd-inhibit --what=sleep:idle:handle-lid-switch` as a
    transient unit;
  - masks the sleep targets with `systemctl mask --runtime`;
  - stops and runtime-masks the distro's update timers and services (table
    below).

  All of it lives in `/run`, which is in neither the tree nor a manifest, and
  a reboot undoes it. bs1-backup and bs1-verify already stop the snapshot
  timers (bs1-common `SNAPSHOTTERS`) and the ostree and ABRoot updaters
  (`UPDATERS`), and only while they run; none of the other updaters.
- **If an update slips in before the hold,** `verify`'s package check fails.
  The Persistent timers that matter (apt's, at least) wait a random delay
  after boot, so this is unlikely. If it happens, it counts as interference:
  that half is rerun once with no fix. If it happens twice, I ask you (Fix
  policy).
- **One binary of each tool for every distro:** the static builds already on
  the drive. rustic-bko's ran every phase 1 on the drive, and bko-holt's
  ran Aeon's and Artix's of 2026-11-23 and the restores since; it is
  static, so it runs on musl (Void, Alpine, Chimera) and NixOS too. No
  rehearsal of the binaries themselves is owed.
- **Three rows are typed.** Ubuntu 26.10's keystore (row 8, unless Q8 makes
  its B hands-free), Manjaro's GRUB cryptodisk (row 9) and Alpine's mkinitfs
  LUKS (row 10) are attended (`UNLOCK=attended` in their L's `unlock.txt`):
  you type at each restored boot, A's and B's (Order 1). Every other LUKS
  row unlocks from the drive (`UNLOCK=drive`: families B, D, E, N, S and Z),
  and the rest have no LUKS. No console visits: Decision 4's rule (no
  booting older generations) also covers Bazzite's rollback deployment
  (Q7) and Void's older boot environment.

## Per-distro table

| # | Distro (stack) | L: library run for A | Repo | Held besides sleep | Extra checks | Est. A+B |
|---|---|---|---|---|---|---|
| 1 | NixOS 26.05 (Calamares: LUKS2 xfs root, LUKS2 swap, systemd-boot, systemd stage 1; family N) | nixos-26.05-20261116-210139 (X13, Q6); full A+B (Decision 1) | 3.5G | nothing auto by default | both halves (Decision 4): every `nixos-generation-N.conf` entry on the ESP finds its kernel and initrd, no older generation booted; verify's unlock check (`UNLOCK=drive`), LUKS UUIDs the baseline's; B's phase 1 in a boot the drive's key opened | 36 min (A 11, B about 23-25) |
| 2 | Debian 13 (guided LVM-on-LUKS2: VG thinkpad-vg with xfs root, ext4 var and srv, swap; family D keyscript) | debian-13-20261116-151543 (X13, Q6) | 3.5G | apt-daily{,-upgrade}.timer, unattended-upgrades if installed | both halves: LUKS UUID (`cryptsetup luksUUID`) the baseline's, verify's unlock check (`UNLOCK=drive`); B: plan.txt shows the partition stage runs and nothing to release (the rescue opens no LUKS, so thinkpad-vg is never active) | 30 min |
| 3 | CachyOS (ZFS root, Limine, no LUKS) | cachyos-20261116-163026 (X13, Q6) | 6.6G | none known (item 0 lists the active timers) | `zpool status -x`; the pool keeps the source's name (the baseline's), not rpool_bko; B: plan.txt shows no pool to export (the stick never imports one) | 30 min |
| 4 | Fedora 44 Xfce (btrfs / and /home subvols, ext4 /boot, vfat ESP, no LUKS) | fedora-44-20261116-141133 (X13, Q6) | 3.1G | dnf-makecache.timer, any dnf-automatic timer, packagekit | SELinux labels, compared by the manifest when the baseline has xattrs | 25 min |
| 5 | Ubuntu 24.04.5 (ext4 /, vfat ESP, shim+GRUB, no LUKS) | ubuntu-24.04-20261116-133442 (X13, Q6) | 1.4G | apt timers, unattended-upgrades, snapd.service and snapd.socket | none | 20 min |
| 6 | Zorin OS 18 (UEFI, plain ext4 /, vfat ESP) | zorin-18-20261116-181641 (X13, Q6) | 4.3G | apt timers, unattended-upgrades, packagekit, snapd if present | none beyond verify: its first verify on 2026-11-16 failed only on `var/lock/{asound.state,card0}.lock`, expected since f49da8b; the re-verify passed, so A is the first whole run with that rule | 35 min |
| 7 | Alpine 3.24 (ext4, OpenRC, UEFI) | alpine-3.24.4-20261116-192831 (X13, Q6) | 362M | none | before B's phase 1, `efibootmgr` is present and xfs loads at boot: L was taken after fe9ef31's adopt, so the restored system mounts mybig512 with no `modprobe` by hand | 15 min |
| 8 | Ubuntu 26.10 (installer's ZFS + encryption: rpool natively encrypted, its key in a LUKS2 keystore zvol, dm-crypt random-key swap, dracut; attended) | ubuntu-26.10-20261119-061810 (accepted as a PASS by the user on 2026-11-19; the drive's RESULT says `FAIL: 1 checks`) | 3.6G | apt-daily{,-upgrade}.timer, unattended-upgrades (on the dev release it spins and blocks shutdown; `to-stick` kills it since 03fc030), snapd.service and snapd.socket | both halves: `zpool status -x`, the pool keeps the name rpool (not rpool_bko), the keystore zvol and encryption root the baseline's; A: the one known difference, `etc/zfs/zfs-list.cache/rpool` stale in L itself, is accepted again, nothing else; B: if Q8 is yes, `backshot1 adopt` and a drive-unlocked reboot come first, so B is hands-free (family S volume K, 49c78eb, first time on hardware) and verify's unlock check (`UNLOCK=drive`) runs; if not, B is typed too | 30 min, plus two typed boots |
| 9 | Manjaro (Calamares "Encrypt system": LUKS1 btrfs root with /boot inside, GRUB_ENABLE_CRYPTODISK, LUKS1 swap, mkinitcpio encrypt + openswap; family A, attended) | manjaro-20261120-184937 | 2.5G | pamac's timers, if item 0 finds any active | both halves typed at the restored boot: GRUB's prompt, then openswap's (its `unlock_method="password"`, as on the source); keyslot 1 (`/crypto_keyfile.bin`) still opens the root (490571c); LUKS1 kept and the LUKS UUIDs the baseline's. `cycle` refuses family A (c4917fc), so both halves run step by step (Tooling 7) | 30 min, plus four typed prompts |
| 10 | Alpine 3.24 (setup-alpine sys + crypt: LUKS2 ext4 root, 300M vfat ESP as /boot, swap, GRUB, mkinitfs `cryptroot=`; family A, attended) | alpine-3.24.4-20261121-150839 | 367M | none (OpenRC) | both halves typed at the restored boot; the "Alpine Linux" NVRAM entry (made by hand at the install) is made again; mdev's `/dev/dm-0` passes the coverage check (7dc8bab); efibootmgr present and xfs loads, as row 7 | 20 min, plus two typed boots |
| 11 | openSUSE Aeon 20261121 (tik in fallback mode, a passphrase, not TPM: LUKS2 + btrfs with 13 subvolume mounts, read-only root snapshot, 4 GB ESP; family S) | aeon-20261121-20261123-134515 (the laptop runs Aeon now) | 3.0G | transactional-update.timer (it ran on the laptop at 01:20 on 2026-11-24), rebootmgr if active; snapper's timers are held by bs1-backup already | verify's unlock check (`UNLOCK=drive`); the 12,115 gained `btrfs.compression` xattrs expected (a013b7a); the root still read-only; mybig512 mounts at `/run/bs1-backup` (3cc5ea1) | 25 min |
| 12 | Bazzite 44 (ostree, composefs, btrfs, bootupd shim + GRUB, SELinux, two deployments; no LUKS) | bazzite-44-20261117-175231 | 7.0G | uupd.timer (Persistent: bs1-backup's `UPDATERS` stop it only while backing up), rpm-ostreed-automatic | check-boot of the default deployment; both deployments listed by `rpm-ostree status` as in the baseline; the rollback deployment isn't booted (Q7); SELinux labels, compared by the manifest | 50 min (its 2026-11-17 round was about 20 min with both check-boots; about 15 without the rollback's, est.) |
| 13 | Proxmox VE 9.2.2 (ZFS RAID0 rpool, proxmox-boot-tool systemd-boot ESP out of fstab, static vmbr0 address, no swap; os-release says Debian 13) | debian-13-20261119-182843 | 1.4G | apt-daily{,-upgrade}.timer, pve-daily-update.timer | `zpool status -x`, the pool keeps the name rpool; `proxmox-boot-tool status` lists the ESP; pve-cluster and pveproxy active, `/etc/pve` mounted, root's authorized_keys link into `/etc/pve/priv` resolves | 20 min |
| 14 | Void musl (encrypted ZFS zroot, aes-256-gcm, ZFSBootMenu v3.1.0 EFI, BEs void and void-6.18, zroot/home, zroot/var/log, runit, Xfce; family Z) | void-20261119-133257 | 2.1G | none (runit: bs1-hold does nothing) | verify's unlock check (`UNLOCK=drive`, `bs1.zbm=zroot:drive`); `zpool status -x`, pool zroot, both BEs present, `/etc/hostid` the baseline's; the older BE isn't booted (Decision 4's rule) | 25 min |
| 15 | Devuan 6 (ESP, ext2 /boot, LUKS2 argon2id -> LVM devvg: btrfs root @ @home @snapshots, XFS var, swap; sysvinit, eudev, elogind; family D) | devuan-6-20261118-134117 | 2.5G | none (sysvinit) | verify's unlock check (`UNLOCK=drive`), LUKS UUID the baseline's; B: plan.txt shows nothing to release (the rescue opens no LUKS, so devvg is never active) | 20 min |
| 16 | Kali 2026.3 (guided encrypted LVM: LUKS2 -> LVM xfs /, ext4 /var, btrfs /srv, swap; ext4 /boot; initramfs-tools; family D) | kali-2026.3-20261121-132446 | 5.8G | apt-daily timers, if enabled | verify's unlock check (`UNLOCK=drive`), LUKS UUID; `to-stick`'s check of the stick's keys (33cb06c, 78e2f29) runs for the first time on hardware | 45 min (489k files) |
| 17 | openSUSE Tumbleweed 20261119 (Agama: shim + systemd-boot by sdbootutil, LUKS2 -> LVM system: btrfs root booted from @/.snapshots/1/snapshot, swap; SELinux; family S) | opensuse-tumbleweed-20261119-20261121-184252 | 1.8G | snapper's timers (bs1-backup already), packagekit if active | verify's unlock check (`UNLOCK=drive`); the running snapshot's boot entries find their initrds (8b75a7d); SELinux 0 AVC; `snapper list` the baseline's | 25 min |
| 18 | AerynOS 2026.11 (GNOME, moss: 1G ESP at /efi, 4G swap, LUKS2 -> XFS /, blsforme; family B) | aerynos-2026.11-20261118-165316 | 2.3G | none known; the sleep targets are masked on the install itself | verify's unlock check (`UNLOCK=drive`: `rd.luks.key` in `/etc/kernel/cmdline.d`, 98cda5d) | 40 min |
| 19 | Arch (KDE, pacstrap: 1G ESP at /boot, LUKS2 -> F2FS with extra_attr, inode_checksum, sb_checksum and compression, zram swap, systemd-boot loading mkinitcpio UKIs; family S) | arch-20261118-180236 | 3.8G | none; the sleep targets are masked on the install | verify's unlock check (`UNLOCK=drive`, the crypttab.initramfs rewrite of f72e69e); both UKIs boot-listed | 30 min |
| 20 | EndeavourOS (systemd-boot with the ESP at /efi, dracut, Calamares LUKS2 root + LUKS swap, btrfs @ @home @cache @log; family S) | endeavouros-20261120-175324 | 3.7G | none known (item 0 lists the timers) | verify's unlock check (`UNLOCK=drive`) for the root and the swap | 20 min |
| 21 | Fedora 45 Workstation (600M ESP, ext4 /boot, LUKS2 -> btrfs root and home subvols, zram, GRUB2 BLS via shim, SELinux; family S) | fedora-45-20261120-053419 (the drive's RESULT says `FAIL: 1 checks`: uresourced's drop-ins, expected since 9baca61, re-compare 0 unexpected) | 4.6G | dnf-makecache.timer, packagekit; its adopt predates 8c3f57c's sleep drop-in, so bs1-hold's sleep mask is what stops GNOME's 15 min idle suspend | verify's unlock check (`UNLOCK=drive`); SELinux 0 AVC; A is the first whole run with 9baca61's rule | 25 min |
| 22 | elementary OS 8 (installer defaults with encryption: ESP at /boot/efi, ext4 /boot, LUKS2 cryptdata -> VG data: root ext4 + swap; family D) | elementary-8-20261118-082412 | 4.3G | apt timers, packagekit (AppCenter's automatic updates) if active | verify's unlock check (`UNLOCK=drive`), LUKS UUID | 25 min |
| 23 | Pop!_OS 24.04 (Clean Install, encrypted: LUKS cryptdata -> LVM data-root ext4, 4G ESP, 4G /recovery vfat, kernelstub's systemd-boot; family D) | pop-24.04-20261120-021008 (the drive's RESULT says `FAIL: 1 checks`: 11 mtime-only depmod and kernelstub changes, expected since db0873c; accepted) | 6.2G | apt timers, pop-system-updater or packagekit if active | verify's unlock check (`UNLOCK=drive`); /recovery and its loader entry back; A is the first whole run with db0873c's rule | 25 min |
| 24 | Linux Mint 22.3 Xfce (LUKS -> LVM vgmint root + swap_1, ext4 /boot, shim + GRUB; finished by hand after ubiquity crashed; family D) | linuxmint-22.3-20261120-082449 | 2.7G | apt timers, mintupdate's automation timers if enabled | verify's unlock check (`UNLOCK=drive`), LUKS UUID | 30 min |
| 25 | MX Linux 25.3 Xfce (Debian 13 base, sysvinit, plain /boot, LUKS ext4 / and /home, keyscript decrypt_keyctl; family D; its runs are named debian-13) | debian-13-20261121-065025 | 3.0G | none (sysvinit) | verify's unlock check (`UNLOCK=drive`) for both volumes; MX's login churn rules (1085514) | 20 min |
| 26 | Artix xfce-dinit (basestrap: GRUB, ESP at /boot, LUKS2 -> btrfs @ @home, mkinitcpio encrypt; family E) | artix-20261123-105657 | 2.0G | none (dinit) | verify's unlock check (`UNLOCK=drive`); ESP mtimes right with the FAT's time offset (5e4ee13) | 20 min |
| 27 | Artix OpenRC (by hand: 1 GiB ESP, ext4 /boot, LUKS2 ext4 root, mkinitcpio encrypt; family E) | artix-20261118-105750 | 1.7G | none (OpenRC) | verify's unlock check (`UNLOCK=drive`: the bs1key hook loads the drive's xfs, 7711a20) | 20 min |
| 28 | Rocky 10.3 (Anaconda Automatic, Minimal: 600M ESP, xfs /boot, VG rlm on a plain partition: xfs root and home, swap; SELinux enforcing; no LUKS) | rocky-10.3-20261119-202952 | 724M | dnf-makecache.timer | SELinux Enforcing, 0 AVC; B: plan.txt shows whether the stick activated rlm, and if so load releases it (the one row with LVM on a plain partition) | 15 min |
| 29 | openSUSE Leap 16.1 (Agama defaults: btrfs @ subvols, snapper, default subvolume @/.snapshots/1/snapshot, GRUB2, SELinux, swap partition; no LUKS) | opensuse-leap-16.1-20261120-031028 (its second restore, after d18b1e8) | 2.4G | snapper's timers (bs1-backup already), packagekit if active | the default subvolume, `snapper list`, Enforcing and 0 AVC (the hidden mount points' labels, d18b1e8) | 25 min |
| 30 | Garuda Xfce LTS 260819 (Calamares: btrfs @ @home @root @srv @cache @log @tmp + .snapshots in @, 300M ESP at /boot/efi, 16.8G swap; no LUKS) | garuda-20261118-095037 | 3.1G | snapper's timers (bs1-backup already) | `snapper list` the baseline's | 25 min |
| 31 | openSUSE Tumbleweed 20261116 (XFS /, btrfs /home, vfat ESP at /boot/efi, swap; no LUKS) | opensuse-tumbleweed-20261116-20261118-023201 | 2.9G | packagekit if active | none beyond verify | 25 min |
| 32 | NixOS 26.05 (F2FS /, vfat ESP at /boot, systemd-boot; no LUKS) | nixos-26.05-20261118-032308 | 3.8G | nothing auto by default | each `nixos-generation-N.conf` entry finds its kernel and initrd on the ESP (Decision 4) | 40 min |
| 33 | Solus 4.9 (F2FS root, swap, clr-boot-manager's ESP at /boot, out of fstab; no LUKS) | solus-4.9-20261117-202222 | 7.5G | none known | qol-assist's rules (f7beb3b) | 30 min |
| 34 | Manjaro (F2FS root, GRUB with /boot on F2FS, ESP at /boot/efi, swap; no LUKS) | manjaro-20261120-170005 | 3.2G | pamac's timers, if item 0 finds any active | none beyond verify | 25 min |
| 35 | Slackware64-current, pinned 20261121 (kernel 7.3.5, ELILO, ESP + ext4 root, rc.d; no LUKS) | slackware-16.0-20261123-042127 | 4.7G | none (rc.d) | the ELILO entry boots; dhcpcd's ntp.conf rule (b7cf1d8) | 35 min |
| 36 | Chimera (GNOME, ext4 /, ESP as /boot, systemd-boot + gen-systemd-boot, dinit; no LUKS) | chimera-20261118-040321 | 1.8G | none (dinit) | none beyond verify | 20 min |
| 37 | KDE neon 24.04 Testing Edition (ext4 /, ESP at /boot/efi, swap; no LUKS) | neon-24.04-20261117-193337 | 3.3G | apt timers, packagekit (Discover) if active | none beyond verify | 20 min |

Each L is the newest run of its stack whose own restore passed, by the
drive's `runs/` as listed read-only on 2026-11-24. The machine is the disk
serial in the run's `layout.json`: X13 214532A60C34, A100 S7XMNF0Y787118.
Rounds are as the run's note gives them, or, marked est., the restore's
start to verify's end on the drive plus 3 min for `to-stick`.

| Run | Machine | Last restore |
|---|---|---|
| ubuntu-24.04-20261116-133442 | X13 | PASS 2026-11-16, 5 min round |
| fedora-44-20261116-141133 | X13 | PASS 2026-11-16, 7 min |
| debian-13-20261116-151543 | X13 | PASS 2026-11-16, 8 min, unattended |
| cachyos-20261116-163026 | X13 | PASS 2026-11-16, 9 min |
| zorin-18-20261116-181641 | X13 | FAIL on var/lock only, re-verify PASS after f49da8b; about 10 min round (its note; the 2026-11-17 cut used 8 min, est.) |
| alpine-3.24.4-20261116-192831 | X13 | PASS 2026-11-16, 4 min, after fe9ef31 |
| nixos-26.05-20261116-210139 | X13 | PASS 2026-11-16, 10 min, hands-free cycle |
| ubuntu-26.10-20261119-061810 | A100 | accepted as PASS 2026-11-19 (RESULT `FAIL: 1 checks`, the stale zfs-list.cache), 9 min est., attended |
| manjaro-20261120-184937 | A100 | PASS 2026-11-20, attended (the prompt waited hours; 8 min est. without it) |
| alpine-3.24.4-20261121-150839 | A100 | PASS 2026-11-21, attended, 5 min est. |
| aeon-20261121-20261123-134515 | A100 | PASS 2026-11-23, 7 min est. |
| bazzite-44-20261117-175231 | A100 | PASS 2026-11-17, about 20 min with both check-boots, est. |
| debian-13-20261119-182843 (Proxmox) | A100 | PASS 2026-11-19, 5 min |
| void-20261119-133257 (ZBM) | A100 | PASS 2026-11-19, 7 min |
| devuan-6-20261118-134117 | A100 | PASS 2026-11-18, 5 min est. |
| kali-2026.3-20261121-132446 | A100 | PASS 2026-11-21, 13 min est. |
| opensuse-tumbleweed-20261119-20261121-184252 | A100 | PASS 2026-11-21, 7 min |
| aerynos-2026.11-20261118-165316 | A100 | PASS 2026-11-18, 12 min est., hands-free cycle |
| arch-20261118-180236 | A100 | PASS 2026-11-18, 8 min |
| endeavouros-20261120-175324 | A100 | PASS 2026-11-20, 6 min est. |
| fedora-45-20261120-053419 | A100 | accepted as PASS 2026-11-20 after 9baca61's re-compare (RESULT `FAIL: 1 checks`), 7 min est. |
| elementary-8-20261118-082412 | A100 | PASS 2026-11-18, 7 min |
| pop-24.04-20261120-021008 | A100 | accepted as PASS 2026-11-20 after db0873c's re-compare (RESULT `FAIL: 1 checks`), 7 min est. |
| linuxmint-22.3-20261120-082449 | A100 | PASS 2026-11-20, 8 min est. |
| debian-13-20261121-065025 (MX) | A100 | PASS 2026-11-21 on re-verify after 1085514, 6 min est. |
| artix-20261123-105657 (dinit) | A100 | PASS 2026-11-23, 5 min est. |
| artix-20261118-105750 (OpenRC) | A100 | PASS 2026-11-18, re-verify, 6 min est. |
| rocky-10.3-20261119-202952 | A100 | PASS 2026-11-19, 4 min |
| opensuse-leap-16.1-20261120-031028 | A100 | PASS 2026-11-20 (second restore, after d18b1e8), 7 min |
| garuda-20261118-095037 | A100 | PASS 2026-11-18, 7 min |
| opensuse-tumbleweed-20261116-20261118-023201 | A100 | PASS 2026-11-18, 7 min |
| nixos-26.05-20261118-032308 | A100 | PASS 2026-11-18, 12 min (329e128) |
| solus-4.9-20261117-202222 | A100 | PASS 2026-11-17 on re-verify after f7beb3b, 8 min est. |
| manjaro-20261120-170005 | A100 | PASS 2026-11-20, 7 min est. |
| slackware-16.0-20261123-042127 | A100 | PASS 2026-11-23 on re-verify after b7cf1d8, 11 min est. |
| chimera-20261118-040321 | A100 | PASS 2026-11-18, 5 min est. |
| neon-24.04-20261117-193337 | A100 | PASS 2026-11-17, 6 min est. |

The binaries, by each run's `run.log`: every run on the drive logged
rustic-bko f41a91c7… (0.11.4+backotter.16), the staged one. bko-holt's
sha256 differs by run. The staged one, 86152cb6… (2026-11-23 11:56,
Artix's 5e4ee13 build, `--version` still 0.2.6), made only the Aeon and
Artix dinit L's. The L's of 2026-11-16 to 18 logged 3aeccd87…; the others
745ad9f3…, 19663774…, 20513fee…, f6ce4069… or f90ca6e5… (Slackware's
-current build). So for 35 of the 37 rows, A is today's bko-holt loading an
older build's dump.

**Excluded runs on the drive.** They stay where they are (nothing on the
drive is deleted without asking you), but no step names them:
- alpine-3.24.4-20261116-192621: its baseline was taken before adopt
  installed efibootmgr; never restored.
- nixos-26.05-20261116-200906: attended LUKS, before family N.
- nixos-26.05-20261116-205909: an accidental duplicate; never restored.
- aeon-20261121-20261123-131915: FAIL on the `btrfs.compression` xattrs
  before a013b7a; 134515 replaces it.
- aerynos-2026.11-20261118-145340 and -160035: attended (family A), before
  family B; 165316 replaces them.
- artix-20261102-20261123-092557: an X13 GRUB-cryptodisk install, abandoned,
  restored but never verified.
- artix-20261123-102328: FAIL (1 check) before 5e4ee13; 105657 replaces it.
- endeavouros-20261120-174923: taken in a typed boot; never restored.
- fedora-45-20261120-052523: superseded by 053419 the same morning; never
  restored.
- manjaro-20261120-182253: the LUKS1 install before 490571c; never restored.
- ubuntu-26.10-20261118-201455 and -202606: an unencrypted ZFS install of
  the evening before (unlock none); never restored.
- ubuntu-26.10-20261119-050205: FAIL (1 check) before f976a09/dc159d1;
  061810 replaces it.
- biglinux-20261117-145933 and vanilla-3.0-20261117-132713: passing X13
  backups, waiting on Q6 (Dropped rows).

**Timings.** A is one cycle round, as in the list above. B is the
preflight, phase 1 (est. about one round) and one round, so about twice A,
plus 2 min. Each round also runs `plan`, one more `check --read-data` of
the backup, about 1 min at these sizes (est.; Q3). So a round of R min gives
A about R+1 and B about 2R+3; the table rounds A+B to the nearest 5 min (as
rows 3 and 7 always did). Typing waits for the attended rows aren't machine
time and aren't counted.

Total about 16 h 35 min of machine time (est.), not counting fixes: 3 h 11
min for rows 1-7 and 13 h 25 min for rows 8-37. Without the X13 rows (Q6),
about 13 h 25 min.

**Dropped rows: to return when reinstalled and BackShot1 passes again.**
Each joins the table, with its stack, holds and extra checks from its
BackShot1 notes or the 2026-11-13 draft (git history of this file, 08f427f
and earlier), once a fresh install of it passes BackShot1 on the Thinkpad
(the A100) with its backup on mybig512. Still waiting on 2026-11-24:
- no backup on the drive (their passes were on the X13, before the
  2026-11-16 wipe): Gentoo (OpenRC, LVM; gentoo-2.18-20261112-185749),
  PikaOS 4 (rEFInd, booster; pika-4-20261114-134728), AnduinOS 2.0.5
  (anduinos-2.0.5-20261114-153639), Guix 1.5.0 (guix-20261114-114854),
  Debian 12 (LUKS2 + LVM, XFS; debian-12-20261110-141705), CachyOS's LUKS
  btrfs install (cachyos-20261113-223735), Void musl on plain btrfs with GRUB
  (void-20261113-152153), Chimera on ZFS (chimera-20261112-092235; its ext4
  install is row 36), Debian 13 on plain LVM (debian-13-20261110-055903);
- a passing backup on the drive, but made on the X13, so they join only if
  Q6 accepts X13 backups: BigLinux (biglinux-20261117-145933, 4.2G, btrfs @
  and @home, no LUKS, unattended) and Vanilla OS 3
  (vanilla-3.0-20261117-132713, 5.1G, ABRoot on LVM thin, LUKS /var asked by
  ABRoot's own init hook, family A, attended; its other-root boot needs the
  console, and by Decision 4's rule would not be booted);
- the old table's Ubuntu 26.10, Manjaro, EndeavourOS, Tumbleweed, Bazzite,
  Alpine, NixOS (btrfs, 7 subvols), Fedora 44 and Debian 13 rows are replaced
  by the installs above;
- possible second runs, not yet installed: Proxmox on btrfs (its tech
  preview), Aeon in TPM mode (a restored disk needs the recovery key).

## Order

The front-loading rule (2026-11-13: what needs a typed passphrase or a
console visit goes first) applies again: three rows are attended. The run is
about 16 h 35 min of machine time (est.), so it spans sittings: the
attended block while you're there, then the driver goes on unattended and
stops at the first FAIL (Fix policy).

1. **The attended block first, about 1 h 20 min (est.) plus the typing:**
   Ubuntu 26.10 (row 8, the biggest attended layout: ZFS, keystore), then
   Manjaro's GRUB cryptodisk (row 9), then Alpine's LUKS (row 10). Each
   needs you at A's restored boot and again at B's: 2 prompts on Ubuntu (1
   if Q8 makes its B hands-free), 4 on Manjaro (GRUB's, then openswap's), 2
   on Alpine. `cycle` refuses attended runs, so the driver runs their steps
   one by one and pushes NEED-PASSPHRASE when the restored system hasn't
   answered a minute or two after its reboot (README, "Attended runs go
   first"). The block ends on Alpine's B; the next row starts from that
   running system and needs nobody, so you can leave after Alpine's last
   prompt.
2. **The canary for the hands-free path: Aeon (row 11).** Its L is the newer of
   the two backups made by the staged bko-holt (Artix dinit's, row 26, is
   the other), so A is today's tools on today's
   backup, the first `cycle --hold --load-wipes`, and the driver end to end.
3. **Then the biggest layouts, while the fixes are cheap:** Bazzite (ostree),
   Proxmox (ZFS), Void ZBM (encrypted ZFS), CachyOS (row 3, ZFS), Debian 13
   (row 2, LVM on LUKS), Devuan (LVM on LUKS, btrfs + XFS), Kali (LVM on
   LUKS, three filesystems), Tumbleweed's LUKS + LVM + btrfs (row 17).
4. **Then the other LUKS rows:** AerynOS, Arch, EndeavourOS, Fedora 45,
   elementary, Pop!_OS, Mint, MX, Artix dinit, Artix OpenRC, NixOS LUKS
   (row 1).
5. **Then the rest:** Rocky, Leap 16, Garuda, Tumbleweed XFS, Fedora 44,
   Ubuntu 24.04, Zorin, NixOS F2FS, Solus, Manjaro F2FS, Slackware, Chimera,
   KDE neon, and Alpine ext4 (row 7) last, which stays on the laptop (Q5).
6. Consecutive A's wipe different stacks: ZFS under LVM on LUKS, LUKS under
   ZFS, btrfs under F2FS, and so on. That is extra coverage of the script's
   own wipe. If Q6 drops the X13 rows, steps 3-5 go on without them.

## Tooling to build first (about 3-4 h, est.)

Builds and rehearsals go to Chopin, Cinqcent, Cage or Copland through
`testvm/pick-host`. No binary is built: today's are on the drive.

0. **Read-only checks first,** each from the session:
   - What the laptop runs (on 2026-11-24: Aeon, row 11's L restored, with
     mybig512 at `/run/bs1-backup`), and that no other session is using it
     or mybig512.
   - `backshot1 list`: the 37 L's are present with `PHASE1_OK`, and the
     drive's free space, recorded (289G free on 2026-11-24).
   - Each L's `baseline/unlock.txt` (`attended` for rows 8-10, `drive` for
     the other LUKS rows, `none` for the rest; read 2026-11-24) and its
     layout's firmware mode, `uefi`: what `cycle` refuses otherwise.
   - For the X13 rows (Q6), `backshot1 plan L` from the rescue (plan only,
     nothing written) shows whether load maps the X13's disk onto the
     A100's.
   - The staged binaries: `--version` (0.2.6, 0.11.4+backotter.16) and
     sha256 (86152cb6…, f41a91c7…), against each L's `run.log` and Chopin's
     holt-bin.
   - The firmware's boot entries, counted (Risks).
   - The hands-free set-up (README, "The hands-free cycle"): the stick in a
     laptop port, `BootMode` `Diagnostics`.
   - The stick's `/cdrom/.disk/backotter.json`, recorded.
   - `keys/disk-passphrase` is non-empty with no trailing newline (Debian
     13's and NixOS's fallback).
   - On each distro, at its first boot, the active timers (`systemctl
     list-timers`), for the holds.
1. **The `--erase` path.** b3af94d (`--erase` on a disk whose table already
   matches) and d965dc5 (table-less disks) record no review in git. If Q2 is
   yes, B relies on them alone, so they get one High review at the staged
   version, and the holt suite runs once in a warm dir. Fix what the review
   finds, as usual.
2. **The binaries:** none to build. A fix to either tool is rebuilt the way
   the staged one was (bko-holt static-pie into Chopin's holt-bin) and
   restaged (Fix policy).
3. **`bs1-hold on|off|status`** (on the drive): the runtime holds above. It
   does nothing on non-systemd systems (Alpine, Devuan, MX, Void, Artix,
   Chimera, Slackware), which have no automatic updates here. `backshot1 stage`'s file list gains it.
4. **`backshot1 find-host`** finds the installed system by MAC, as
   `find-rescue` does, for when mDNS fails. **`backshot1 nixos-gen N`** does
   the generation listing, the closure check and, if Q4 says so, the
   generation boot.
5. **`bs1-restore --load-wipes`** (if Q2 is yes), B only:
   - The script's own `vgchange -an`, `mdadm --stop`, `zpool export`, its
     "still active" check and `wipefs` are skipped, so `bko-holt load
     --erase` releases any holders (Rocky's VG at most), zeroes the ends
     and writes the table itself.
   - The serial, plan, other-disks and nothing-mounted guards stay.
   - It fails unless plan.txt says the partition stage runs, and if the
     partition, format or restore stage reports "done already".
   - In every mode, bs1-restore also logs `rustic-bko --version` and both
     binaries' sha256, as bs1-backup does (today it logs only `bko-holt
     --version`), so A's logs carry them too.
6. **`backshot1 cycle` gains `--hold` and `--load-wipes`** (if Q3 is yes):
   `--hold` runs `bs1-hold on` between `wait-host` and `verify`;
   `--load-wipes` passes through to `plan` and `phase2`.
7. **`bs1-all QUEUE [--only D] [--from STEP] [--dry-run]`**, the driver on
   the Mac:
   - Per row: A's cycle, the extra checks for A, preflight, phase 1, B's
     cycle, the extra checks for B. For an attended row (8-10), which
     `cycle` refuses, the same steps one by one (`to-stick`, `plan`,
     `phase2`, `reboot`, `wait-host`, `bs1-hold on`, `verify`), with a
     NEED-PASSPHRASE push a minute or two after each reboot into the
     restored system, and `wait-host` waiting for you rather than failing at
     10 min. If Q8 is yes, Ubuntu 26.10's row runs `backshot1 adopt` and a
     drive-unlocked reboot between A and B's phase 1.
   - It keeps per-step state in `$BS1_STATE/bs1all/`, a fixed directory
     rather than the default `$TMPDIR`, so it can resume. It writes a
     journal: `STEP OK`, `FAIL`, `WAIT-USER`, `NEED-PASSPHRASE`, `ALL-DONE`.
   - It stops at the first FAIL.
   - It runs under `caffeinate -dims`, and the session holds the Mac awake.
   - The session's Monitor watches the journal and sends the push
     notifications.
   - Before each phase 1, it checks for at least 80 GB free on the drive and
     for `efibootmgr` (`to-stick` needs it on every UEFI system).
   - Timeouts: `cycle`'s own (`to-stick` 7 min, `wait-host` 10 min, the
     stick silent 10 min), with `BS1_RESTORE_LIMIT=60` (the longest round on
     2026-11-16 to 23 was about 20 min, Bazzite's). A system that isn't
     back fails `wait-host` at 10 min, which fails the cycle: a FAIL and a
     push. For a `drive` system that most likely means the prompt (the
     drive's key failed).
   - The firmware's boot entries: `cycle` compares each round only with its
     own round 1, so with one round per cycle its safety net never fires. The
     driver counts them after every cycle and stops once there are more than
     8 above item 0's count.
   - A WAIT-USER step (none planned since Decision 4; Q6's plan check, if it
     needs your answer) waits until the session writes a `continue` file
     after you answer in chat.
8. **Rehearsals,** in parallel:
   - `bs1-qemu.py` gains a `--load-wipes` pass-through. It restores onto the
     guest's own disk, which is exactly B's case. It runs on:
     - `alpine324` (smoke);
     - `nixos2605`;
     - `ubuntu26-luks` (LVM on LUKS recreated over itself, with nothing to
       release, as on Debian 13; there is still no Debian initramfs-tools
       LUKS guest, README "Rehearsal");
     - `cachyos-zfs` (a ZFS root recreated over its own pool, none
       imported).
   - The bs2 leftovers set, strict (`bs2-host leftovers --run
     lo-load-wipes`). Its L3 was refused before, which is the held-VG case.
   - `bs1-all --dry-run`.
   - One High review covers items 3 to 7, with `--load-wipes` read first.

After that, `backshot1 stage TOOLS_DIR` runs once, from the running system,
so the drive's scripts are main's. TOOLS_DIR is a directory on the Mac
holding copies of the drive's own `backshot1/bin/bko-holt` and `rustic-bko`,
fetched first with `scp`, so the staged sha256s don't change.

## PASS criteria

- **A distro passes when both halves pass:**
  - **A:**
    - phase2's `RESULT: PASS`: 0 differences, no stage skipped, and status
      says "every stage is done";
    - then `verify L` gives `RESULT: PASS`, including "firmware booted the
      restored ESP's entry";
    - the extra checks marked for A.
  - **B:**
    - `PREFLIGHT OK`, then `PHASE1_OK` (`check --read-data`);
    - plan.txt shows that the partition stage runs and nothing to release
      (the rescue opens no LUKS and imports no pool), except on Rocky (row
      28), where load may release the VG rlm if the stick activated it;
    - phase2's PASS, then `verify B`'s PASS;
    - the extra checks in the table.
- **BackShot1All passes when:**
  - every row passes (rows 1-7 as Q6 decides);
  - every restore log and every phase 1 log records the binaries' sha256:
    the staged ones, or, for a distro rerun after a fix, the final ones (Fix
    policy);
  - the laptop is left booted with `bs1-hold off` applied.

## Fix policy

The user's rule applies: "keep running, testing, and running again until you
succeed."

- **When a step fails,** the driver stops and you get a push notification. I
  diagnose from `runs/RUN/restores/STAMP/` and the bko report, then sort the
  failure:
  - **Interference** (an update, sleep, mDNS, power): rerun, no fix.
  - **An expectation in the harness** (a manifest EXPECTED path, like
    f49da8b's var/lock): a small fix with no separate review
    (fix-cycle-speedups #4).
  - **A tool bug:** fixed by a High-effort agent that has a file map. A
    change to what gets erased, mounted or restored gets its own High review.
- **Each fix:**
  - tests run once, in the warm dir;
  - commit on main, no push;
  - rebuild and restage (from the installed system, or with `scp` to the
    rescue into `/mnt/bs1-backup/backshot1/bin`);
  - then `bs1-all --from STEP` resumes that distro.
- **After a failed phase2,** the laptop sits wiped on the rescue, and the
  retry starts there (`find-rescue`, then that half's steps by hand: `cycle`
  starts from the installed system).
- **Earlier distros are rerun (their B only)** when the reviewer judges that
  the fix touches a path they used.
- **I stop and ask you when:**
  - the same step fails 3 times after fixes, or interference repeats;
  - an A fails because of the backup itself (something a fresh dump would
    have recorded): fix load, or record it as a limit and skip that distro
    (reinstalling is out of scope);
  - a fix would touch anything but the internal disk;
  - the drive falls below 80 GB free;
  - item 0 counts 30 or more firmware boot entries;
  - an OS update, or a re-adopt on CachyOS, would be needed (Ubuntu 26.10's
    re-adopt is Q8's, not this);
  - an X13 row's A fails on something tied to the hardware (Q6).

## Your part

- **Before the start:**
  - Plug the Thinkpad's PSU in directly, not through the hub (the
    2026-11-08 low-power stall).
  - Leave the lid open and the USB Ethernet adapter in.
  - Leave the rescue stick in one of the laptop's own ports (README, "The
    hands-free cycle") and mybig512 plugged in, and **unplug the spare USB
    sticks** (phase2 refuses unexpected disks).
  - Keep other sessions off the Thinkpad and mybig512 until the end.
  - Keep the Mac on power. The driver keeps it awake.
- **At the start, the attended block (Order 1, about 1 h 20 min, est.):**
  type `keys/disk-passphrase` when a push asks: Ubuntu 26.10 twice (once if
  Q8 is yes), Manjaro four times (GRUB, then openswap, at each of its two
  restored boots), Alpine twice. After Alpine's last prompt you can leave;
  the rest is unattended.
- **Any time:** if a push says the laptop isn't back, tell me what's on its
  screen. If it shows no OS after a failed restore, pick the stick from the
  F12 menu.
- **At the end:** say which OS should stay (Q5).

## Risks

- **The backup drive filling.**
  - B adds one repository per row, about the size of L's: 22.8G for rows
    1-7 and about 98G for rows 8-37, so about 121G in all (est.), against
    289G free on 2026-11-24. Item 0 records the free space.
  - A rerun of phase 1 adds another.
  - The 80 GB check runs before each phase 1, and nothing on the drive is
    deleted without asking you.
- **NVMe wear.** 74 restores (37 A's, 37 B's) of a few GiB to about 20 GiB
  each (Bazzite, Solus, CachyOS the largest): under 1 TB (est.), a small
  fraction of a consumer NVMe's rated endurance.
- **The rescue image.** `bs1-restore` uses the drive's `bin/`, so the stick
  supplies only:
  - the kernel and OpenZFS;
  - the mkfs tools;
  - the LVM, md, LUKS and ZFS tools load runs to recreate the layout.

  Keep the stick that passed the runs of 2026-11-21 to 23 (you reflashed it
  on 2026-11-21; item 0 records its `backotter.json`), and don't rebuild it
  mid-test. After any reflash its BackShot1 keys go back on first (README;
  `to-stick` refuses a stick without them since 33cb06c).
- **mDNS.** Every system answers as `thinkpad` with the pinned host key, but
  avahi can lag. The driver falls back to `find-host`.
- **Firmware boot entries.** The restore's boot stage now deletes any entry
  for the same partition and loader before making the source's again, and
  `to-stick` deletes its own "BKO rescue", so the count should hold steady.
  The driver's count across cycles (Tooling 7) is the safety net; a full
  NVRAM store can make creating an entry fail, or worse on some firmware.
- **LUKS.** Every LUKS row but 8-10 unlocks from mybig512, so one sitting
  at its prompt means the drive's key failed: a FAIL and a push, not a
  wait. The queue can't go on past it, since the next step starts from that
  system. Rows 8-10 wait for you instead (Order 1).
- **Long unattended stretch.** About 15 h after the attended block (est.).
  The driver stops at the first FAIL and pushes; the laptop then waits, on
  the rescue or the last system, until the fix (Q10).
- **Alpine and xfs.** A fresh Alpine boot needs xfs loaded by hand before
  its first mount of mybig512; L was taken after adopt fixed that for later
  boots, and DRIVE now loads xfs itself, so the restore of L shouldn't need
  it. If it does, that is a FAIL of the extra check, not a hand fix.

## Decisions (2026-11-17)

The user answered Q1-Q5 on 2026-11-17. Where the text above says "if Q1 is
yes" and the like, these govern. The 2026-11-24 widening opens Q6-Q10
(below).

1. **NixOS: full A+B** (the user's choice). A restores 210139 with today's
   tools like every other row; NixOS's row is about 36 min (est.), the total
   about 3 h 5 min (est.).
2. **`--load-wipes` in B:** yes, B only, after item 1's review (my
   judgment, as recommended).
3. **Reuse `backshot1 cycle` for A and B:** yes, with `--hold` and
   `--load-wipes` (my judgment, as recommended).
4. **NixOS's older generations: no boot** (the user, 2026-11-17: BackShot1
   is a bare-metal restore; booting a previous generation tests NixOS's boot
   loader, not bko). Kept, hands-free and seconds long, in verify of both A
   and B: every `nixos-generation-N.conf` entry on the ESP finds its kernel
   and initrd there (bs1-manifest excuses `boot/EFI/*`, so nothing else
   checks them). Options (a) and (c) of the earlier draft are dropped.
5. **What stays on the laptop:** doesn't matter (the user); Alpine, the
   last in the order.

Settled or dropped from the 2026-11-13 draft:
- the name (old 1): BackShot1All, as you call it;
- Bazzite's rollback (old 4), EndeavourOS's XFS `--reformat` variant (old
  5) and when the user block runs (old 10): their distros or block were gone
  on 2026-11-17 (since 2026-11-24 Bazzite is row 12, its rollback Q7, and
  the attended block is Order 1);
- the builds (old 7) and the rustic-bko build (old 9): the staged binaries
  are today's tools, and nothing is built;
- pruning firmware entries (old 8): the boot stage no longer piles them up;
  the 30-entry line is now a stop-and-ask in the Fix policy.

## Open questions (2026-11-24)

The widening opens these; each has a recommendation, and nothing runs
before they are answered.

6. **The X13 backups.** Rows 1-7, and BigLinux and Vanilla OS in the
   Dropped rows, were made on the X13 (Micron 256 GB, 214532A60C34). Their
   A is now a restore onto another machine: another disk (Samsung 512 GB),
   firmware and network adapter. `bs1-restore` erases only the A100's disk
   by serial, but whether load maps the X13 layout's disk onto it, and what
   verify then finds, is untested.
   - (a) Keep them: item 0's `plan` check first; a row whose plan refuses,
     or whose A fails only on something tied to the hardware, moves to the
     waiting list rather than getting a fix (BackShot1 is same-machine).
     BigLinux (about 30 min, est., unattended) and Vanilla OS (about 50 min,
     est., attended, so in Order 1) would join as rows 38 and 39.
   - (b) Move rows 1-7 to the waiting list until each is reinstalled on the
     A100. NixOS and Alpine keep other stacks in rows 32 and 10. About 3 h
     less.
   - Recommended: (a), since a cross-machine A costs one plan check to try
     and B is a plain A100 round trip either way.
7. **Bazzite's rollback deployment:** not booted, by Decision 4's rule
   (recommended): both deployments listed, check-boot of the default only.
   Booting it needs the rollback's own sshd up (adopt runs in the default
   deployment only; on 2026-11-17 you got it answering by hand), so it
   would be a console visit and join the attended block.
8. **Ubuntu 26.10's B hands-free:** after A, `backshot1 adopt` and a
   drive-unlocked reboot, so B's restored boot unlocks the keystore from the
   drive (family S volume K, 49c78eb: rehearsed in QEMU only). Recommended:
   yes, the first hardware test of 49c78eb without a reinstall. It is the
   one exception to "no re-adopt"; B is compared with its own baseline, so
   adopt's dracut module doesn't upset it. No: B is typed too.
9. **One row per stack, or per distro:** 37 rows with two each for NixOS,
   Alpine, Artix, Manjaro and Tumbleweed (recommended: each is a different
   layout for load), or one per distro, dropping rows 1, 7, 27, 31 and 34
   (about 2 h less).
10. **One unattended stretch, or by days:** after the attended block the
   driver runs Order 2-5 on its own, about 15 h (est.), stopping at the
   first FAIL (recommended), or Order 2-3 one day and 4-5 the next, so the
   laptop doesn't run overnight.
