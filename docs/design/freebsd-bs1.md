# BackShot1 on FreeBSD: the A100, FreeBSD 15 first, GhostBSD next

2026-11-27. Approved by the user 2026-11-27, every recommendation (D1-D10, Q8 no); ON HOLD: nothing is built until the user says proceed. BackShot1 (testvm/realworld/backshot1/)
has done its bare-metal round trip on the Thinkpad, the Lenovo A100, for
about 35 Linux distros. This spec plans the same round trip for FreeBSD
15.1 on the same machine, and then for GhostBSD. bko-holt already dumps and
loads FreeBSD in QEMU (HoltTest1BSD, docs/design/geli.md, geli2.md,
layload.md). What's missing is everything around it: the backup drive, the
rescue stick, the scripts and the install.

**Conventions.** est. marks an estimate. *Check* marks a fact to settle
before the code that depends on it; *Check on the A100* marks a hardware
fact that only the machine can settle. [inferred] marks a claim from
general FreeBSD knowledge or reasoning, not read in the repo or in FreeBSD's
source. File references are to local `main` at d4aea22. backshot1/README.md
is cited by section name, with its line at d4aea22: the uncommitted README
edit inserts 84 lines at :686, so later lines move down by 84 once it is
committed. F1 left stick/boot-test.py alone: its key login case is a file of
its own, stick/key-login-test.py.
D1-D10 are the decisions, F1-F13 the milestones, Q1-Q8 the open questions.

## Summary

- **The backup drive can't be mybig512.** It is XFS, FreeBSD has no XFS
  driver, and it must never be reformatted or repartitioned. The FreeBSD
  lane gets a drive of its own, UFS2, which the user supplies (D2).
  mybig512 is unplugged for the lane. Every FreeBSD step acts only on disks
  it knows by FreeBSD serial (the internal disk, the lane drive, the stick)
  and stops if any other disk, or anything holding XFS, is present (D3).
- **The rescue stick is a second, FreeBSD stick.** The FreeBSD image gains
  `key-access`, so it starts sshd at boot and lets BackShot1's key in for
  `backotter`, with base's `mdo` in place of sudo, so the driver's
  `root@` = installed system, `backotter@` = stick split still holds (D4).
  The Linux stick isn't touched.
- **The same scripts, with a FreeBSD branch.** `bs1-common` gets a
  `bs1-common-freebsd` that overrides its Linux functions on FreeBSD; the
  step scripts branch on `uname -s` (D8). bash and python3 come from pkg on
  the installed system, and from a tool root on the lane drive on the stick
  (D5), prepended to PATH after bs1-common resets it.
- **bko-holt needs one change first:** `dump --only-disk` on FreeBSD
  (holt/src/lib.rs:93-96), filtering every section of the dump, not only
  the disks. Inactive boot environments are refused, not backed up (D9).
- **The install is remote:** `bsdinstall script` over SSH from the FreeBSD
  stick, detached, with the new root at `/mnt/bko-install` so it can't
  cover the lane drive, from the memstick's own pkgbase repository staged
  on the lane drive (D6). ZFS without encryption first, then UFS, then ZFS
  on GELI (D1).
- **GELI is attended first, and attended rows run step by step** (`cycle`
  refuses them, backshot1:263-266). The installer's GELI layout makes the
  loader ask, and nothing configurable can answer for it; the stick's own
  loader asks too when it boots over that disk. Unattended unlock
  ("family F") uses the installer's boot-pool layout and a `local.lua` that
  reads the passphrase from the lane drive (D7).
- **Rehearsed in QEMU first**, on a farm host from `testvm/pick-host`
  (F7), before anything writes to the A100's NVRAM or formats a real USB
  drive; joined to BackShot1All as a block of its own (D10).
- ~11.5 d of work (est.), plus the user's time: two USB devices, a few F12
  boots, and typed passphrases on the attended GELI runs (two per round).

## Why

- FreeBSD's installer offers ZFS on root and "Encrypt Disks?" in one
  screen. A FreeBSD machine is as likely to be ZFS on GELI as a Linux
  laptop is to be LUKS. bko-holt restores those in QEMU, but never on
  real firmware, a real NVMe or a real NIC.
- geli.md's D7 and geli2.md's E7 left the unattended GELI boot and a
  BackShot1 FreeBSD run to a plan of their own. This is it.
- The FreeBSD rescue image has no recorded boot on real hardware over
  xHCI [inferred: none found in the repo]. stick/freebsd/README.md:104-111
  says only that QEMU's xHCI garbles reads larger than one USB transfer
  and that the QEMU tests therefore use EHCI.

## The machine

From the repo and from earlier sessions' captures (Linux live ISO on the
A100, 2026-11-17 and 2026-11-23):

| | | FreeBSD 15.1 [inferred unless noted] |
|---|---|---|
| Machine | Lenovo IdeaCentre A100 all-in-one, F0J6006UFR, BIOS O6MKT0DA; no lid, no battery, no TPM visible | |
| CPU | Intel N100, 4 E-cores; 16 GB | works on other N100 boxes (forum reports) |
| Disk | Samsung PM9C1a 512 GB, DRAM-less, serial `S7XMNF0Y787118` (backshot1/README.md:8-12) | `nda0` via nvme(4); serial as diskinfo's "Disk ident." (holt/src/other/freebsd.rs:16-46). *Check on the A100* |
| Wired NIC | RTL8168H/8111H, PCI rev 0x15, MAC `1c:83:41:e8:f7:38` (backshot1:41) | re(4), in GENERIC; known watchdog-timeout reports under load (PR 166724). *Check on the A100* |
| USB NICs | RTL8153A, RTL8152, behind a USB 2.0 hub | ure(4) |
| USB | xHCI only (Alder Lake-N) | xhci(4). The image is untested on real xHCI. The lane drive (repository, media, tool root) is on the same xHCI. *Check on the A100* |
| Wi-Fi | MediaTek MT7920 (mt7921e) | no driver in 15.1; irrelevant, the images are wired-only |
| Graphics | Alder Lake-N UHD (i915 on Linux) | efifb console only; no drm-kmod needed on FreeBSD; GhostBSD loads it |
| Firmware | UEFI, Secure Boot off (2026-11-17); prefers the NVMe even with a stick in, so BootNext is needed | FreeBSD's loader is unsigned (stick/freebsd/README.md:8-9). *Check on the A100* after any BIOS reset |

## What is Linux-only today, and how it changes

Classes: **branch** (a FreeBSD branch beside the Linux code), **new** (a
FreeBSD file of its own), **n/a** (nothing to do on FreeBSD, beyond a
guard). Line numbers are main's; sizes are est.

| Piece | What assumes Linux | FreeBSD change |
|---|---|---|
| `thinkpad.conf` | `BACKUP_LABEL=mybig512` (:4) | **new** `thinkpad-bsd.conf`: the lane drive's label, file system and FreeBSD serial (`LANE_SERIAL`), the stick's (`STICK_SERIAL`, from F2), `NEVER_SERIAL=012345678921` and mybig512's GPT partition GUIDs (D3); the driver takes it from `BS1_CONF` (backshot1:38 sources thinkpad.conf by name) |
| `backshot1` (Mac driver), ~120 lines | `DRIVE` (:68): findmnt, `modprobe xfs`, `mount LABEL=`; `run_on_drive` (:71-75): `exec bash`, `sudo -n bash -c`; `net_cidr` (:82-94): `ip`; `host_up` (:120) and `find-rescue` (:181, :185): `sudo -n true`, `/cdrom/.disk/backotter.json`, `lsblk -dno SERIAL`; `stage` (:124-139) works only through `on_host`, on an installed system; `/run/backotter` (:217, :224); `systemctl reboot` (:228); `/proc/sys/kernel/hostname` (:233); `cycle` refuses `attended` (:263-266); efivarfs entry count (:292-298) | **branch** on the conf: `DRIVE` reuses an existing mount of `/dev/ufs/LABEL` (`mount -p`; GhostBSD's desktop may automount it), else runs `fsck -p -t ufs /dev/ufs/LABEL` and mounts it (`mount -t ufs`), POSIX sh, still passed inside single quotes; bash by its full path (the installed system's `/usr/local/bin/bash`, the stick's `$d/backshot1/freebsd/tools/usr/local/bin/bash`); on the stick `mdo` in place of `sudo -n` (D4); marker `/.disk/backotter.json`, tested on both sides (below); serial from `diskinfo -v`; `stage --rescue`, which stages the drive through the stick by tar over SSH before any install exists, with D3's guard; `/var/run/backotter`; `shutdown -r now`; `hostname -s`; entries counted from `efibootmgr` output. Attended rows run step by step (to-stick, plan, phase2, reboot, wait-host, verify), as Linux family A does. `net_cidr` already falls back quietly to `$STATE/net` |
| `bs1-common`, 350-500 lines | `PATH` reset (:8), which drops a tool root put in the environment; `TREE=/run/bs1-src` (:31), `RO_MOUNTS=/run/bs1-ro` (:185), `UNLOCK_MARK=/run/backshot1-unlock` (:608): FreeBSD has no /run, and `mkdir -p` would make one on the source's root, so it ends up in the backup; `this_host` (:54) `/proc`; `internal_disk` (:57-69) lsblk and `/sys/block`; `disk_of`, `backup_disk` (:71-80); `REAL_FS` (:82) has no `ufs`, says `vfat`; `disk_esps` (:94-120); `boot_entry_for` (:126-138) parses Linux efibootmgr; `removable_loader` (:143-162) `mount -o ro` without a type; `root_source`, `internal_mounts`, `check_coverage` (:174, :264-302, :386-415) findmnt/lsblk/blkid, and the coverage check stops on any unmounted file system (:376); `hold_snapshotters`, `hold_awake` (:178-237) have no FreeBSD holds; `on_disk` (:242-253) lsblk; `make_tree`/`drop_tree` (:444-465) `mount --bind`; `package_list` (:478-499) has no pkg; `swap_names`, `failed_units` (:537-553); `zvols_on`, `luks_devices`, `luks_names` (:631-670) lsblk/blkid; `unlock_mode` (:847-906) sees LUKS only; `luks_key_test` (:909-915) | **new** `bs1-common-freebsd`, sourced at the end of bs1-common when `uname -s` is FreeBSD, overriding those functions: PATH with the tool root's `usr/local/bin` first (when set) after :8; `TREE`, `RO_MOUNTS` and `UNLOCK_MARK` under `/var/run` (accepted as churn: var/run/* is on the churn list); `sysctl kern.disks` + `diskinfo -v`, `gpart list` (rawtype, rawuuid), `glabel status`, `mount -p`, `REAL_FS` with `ufs\|msdosfs\|zfs`, `mount -t msdosfs -o ro`, nullfs trees (`mount -t nullfs -o ro`, one per file system, deepest unmounted first), `on_disk` through `gpart` and `zpool status -P`, no zvols and no LUKS, `pkg query '%n\t%v'`, `swapctl -l`, `service -e` (as ht1-bsd.sh's `SERVICE_STATE`), a coverage check that skips `canmount=off` ZFS datasets and the unmounted pool root (bsdinstall's `zroot`, `zroot/usr`, `zroot/var` [inferred]) and stops only on unmounted datasets or partitions holding data, `unlock_mode` and the key test for GELI (`geli attach -C` dry run [inferred, geli(8), FreeBSD 13+; *check* it on an attached provider]), and `hold_periodic`: `service cron onestop` for phase 1, the baseline and verify, and `onestart` after (the base crontab runs periodic daily at 03:01, which after adopt's pkg install includes pkg-audit and pkg-backup, weekly locate, save-entropy every 11 min and newsyslog [inferred]). `space_in_use` (:307-313) and `layout_keys` (:563, already GELI) stay |
| `bs1-adopt`, 120-170 lines | bash bootstrap via `apk` (:38-41); init detection by `/proc/1/comm` (:152); `pkg_install` (:160-193) has no pkg; `start_service` (:425-490) needs init.d; `running` (:492) `/proc`; `set_hostname` writes `/proc/sys/kernel/hostname` (:508), which aborts under `set -e`; `install_tools` (:646-686); `start_mdns` (:705-723) avahi on systemd/OpenRC; `open_firewall` (:748-761) firewalld/ufw only; `mkdir -p /run/sshd` (:919) | **branch**: `pkg install -y bash python3 avahi-app` [inferred names]; init is rc.d by `uname -s`; `sysrc NAME_enable=YES; service NAME restart`; `ps -axo comm`; `sysrc hostname=thinkpad; hostname thinkpad`; `sysrc dbus_enable avahi_daemon_enable`; pf/ipfw only if enabled (GhostBSD); no /run/sshd; checks that `/boot/efi` is in fstab and mounted; `kern.maxphys=65536` in loader.conf only if F2/F3 show it is needed. `allow_root_key_login` (:688-703) is already BSD-safe |
| `bs1-backup`, 40-70 lines | `--only-disk` (:89, :303, :366); `on_disk` (:77); `lsblk` (:349, :421); `findmnt -R` (:410); `sfdisk -d` (:422); `unhinted_check` (:269-283) reads `storage.backup_sources`, which FreeBSD's dump lacks | **branch**: needs holt's `--only-disk` (F4); `gpart show -lp`, `gpart backup nda0`, `mount -p`; a FreeBSD inactive-BE check in place of `unhinted_check` (D9). The rustic-bko backup of the nullfs tree with `--set-atime yes` is unchanged [inferred: never run live on FreeBSD] |
| `bs1-restore` (on the stick), 180-280 lines | `/run/backotter`, `setsid -f` (:51-53); finish's `lsblk`, `dmesg -T`, `journalctl`, `/run/bko-holt/log` (:74-78); `/cdrom/.disk` marker (:90); `findmnt /cdrom`, lsblk disk list and the three-disk guard (:97-102); `/sys/firmware/efi` (:113); python3 helpers (:113, :125); vgchange/mdadm/`udevadm settle`/wipefs/`blockdev` (:140-154); the diff step's `findmnt -R` and `mount -o remount,noatime` loops (:171-189); `/run/bko-holt/restore.globs` (:217); `/proc/self/mounts` (:225); `python3 bs1-efi delete "BKO restored"` (:266); the `removable_loader` + `bs1-efi oneshot` fallback (:287-295) | **branch**: `daemon -o /var/run/backotter/restore.out` in place of `setsid -f` (`daemon -f` would send the output to /dev/null [inferred, daemon(8)], and the driver tails that file, backshot1:224); finish saves `gpart show -lp`, `dmesg -a`, `efibootmgr -v`, `/var/run/bko-holt/log`; `/.disk/backotter.json`; `mount -p /`, `kern.disks`; `sysctl machdep.bootmethod`; python3 from the tool root (D5); before load, `zpool labelclear -f` and `geli clear` on each old partition, then `gpart destroy -F`, checked by `gpart show` (load's `--erase`, other/load.rs:720-721, runs only `gpart destroy -F` and a 1 MiB `dd`: GELI metadata in a provider's last sector, ZFS labels L2/L3 and the backup GPT survive it); the diff's noatime by `mount -u -o noatime` on UFS and `zfs set atime=off` (temporary) on ZFS, put back after; `/var/run/backotter` and `/var/run/bko-holt` (holt/src/load/mod.rs:56-60); `mount -p` for :225; BootNext with `efibootmgr -n -b` on the entry whose device path names the restored ESP's rawuuid, after deleting any "BKO restored"; with no such entry, a one-shot `efibootmgr -a -c -l ESP:/EFI/BOOT/BOOTX64.EFI -L "BKO restored"` left out of BootOrder. The guard keeps "exactly three disks" and adds D3's allow-list |
| `bs1-verify`, 60-100 lines | `system_settled` (:87-105) passes as "no-systemd"; `booted_the_restored_esp` (:164-203) efivarfs, silently skipped (:265); `on_disk` (:213, :256); the rescue-stick check `/cdrom/.disk/backotter.json` (:258), which passes trivially on FreeBSD; `findmnt -M` (:274); `--only-disk` (:296) | **branch**: "rc finished" (`/var/run/...` marker of the last rc.d script [inferred]); BootCurrent from `efibootmgr -v` against `gpart list` rawuuid; `mount -p`; "this isn't the rescue stick" tests `/.disk/backotter.json` too; cron is running again after `hold_periodic`; the `kenv bs1.unlock` check for family F (D7) |
| `bs1-manifest`, 80-150 lines | `os.listxattr` / `os.getxattr` (:556-563) don't exist on FreeBSD, and `AttributeError` isn't caught: it dies at the first file; `FS_IOC_GETFLAGS` (:532-553) degrades to "-"; the churn lists (:70-155, :186-190) are Linux paths | **branch**: extattrs (user and system namespaces) through `extattr_list_link`/`extattr_get_link` by ctypes [inferred API]; record `st_flags` (schg, uarch...) as HoltTest1BSD's mtree does; a FreeBSD churn list (`boot/entropy`, `var/db/entropy/*`, `var/run/*`, `var/db/dhclient.leases.*`, `root/.sh_history`, `var/db/mounttab`, `boot/zfs/zpool.cache`) [inferred], settled by the rehearsal |
| `bs1-to-stick`, 80-120 lines | efivarfs (:17-19); `blkid -L BACKOTTER`, lsblk TRAN (:22-24); `check_stick_keys` (:49-109); `mount -t vfat` (:66); ESP labelled `ESP` (:111); sysfs partition number (:113); Linux efibootmgr options, deleting older "BKO rescue" entries first (:131-144); `systemctl reboot -i` (:159) | **branch**: `/dev/msdosfs/BACKOTTER` on a umass disk of `STICK_SERIAL`; `check_stick_keys` ported, mounting it `-t msdosfs -o ro`; the stick's ESP is MBR slice 1 (stick/freebsd/build.sh:396-407), found by slice, not label; FreeBSD efibootmgr: delete every "BKO rescue" entry (`-B -b NNNN`), save BootOrder, `-a -c -l MNT/EFI/BOOT/BOOTX64.EFI -L "BKO rescue"` (`-c` alone leaves it inactive [inferred, efibootmgr(8)]), restore BootOrder with `-o` (a new entry goes first [inferred]), `-n -b NNNN`, then read BootNext and BootOrder back; `shutdown -r now`. Today it dies at :17 before changing anything |
| `bs1-efi` | efivarfs and sysfs only (:29-50, :148-179) | **n/a**: base efibootmgr replaces it (delete and one-shot are in bs1-restore's branch above) |
| `bs1-luks` | every family is a Linux initramfs or crypttab (:1-80) | **n/a** but a guard: no GELI, nothing to do; GELIBOOT, an `attended` record; family F's adopt step is new (D7), ~150 lines |
| `bs1-unlock-key` | Linux initramfs only | **n/a** |
| `bs1-ostree`, `bs1-abroot` | | **n/a** |
| `bs1-list` | none (plain bash, `${matches[-1]}` needs bash 4.3) | works once bash and the drive are there |
| `bs1-qemu.py`, 200-300 lines | `/proc`, `systemctl`, `mount LABEL=`, `nvme0n1`, the drive made on the Linux host by sfdisk and `mkfs.ext4` (:371-375), sfdisk/blkid fingerprints (:664) | **new** `bs1-qemu-bsd.py` (D8, F7), sharing what it can; the drive made inside the FreeBSD VM (below) |
| `README.md` | "Not covered: the BSDs" (:14-20); the stick "needs a Linux image with key-access" (:40-49); `sudo sh bs1-adopt` (:51-53) | a FreeBSD section, ~100 lines |
| stick/bsd, stick/freebsd | sshd off at boot (build.sh:294-304); `remote-access` gives a one-time password from the console menu (stick/bsd/remote-access:1-12) and creates `backotter` (:23-29); `PermitRootLogin no`, `AllowUsers backotter` (stick/bsd/sshd_config.backotter); no sudo, bash or Python | **new** `stick/bsd/key-access` rc.d script, FreeBSD first (D4). `add-ssh-key.sh` probably works unchanged: it finds partition 3 by `sfdisk -J`, which reads MBR, and checks `backotter/stick.json` (stick/add-ssh-key.sh:43-49), and the FreeBSD image's slice 3 is marker.py's config.img, FAT16 labelled BACKOTTER (stick/make-img-bsd.sh:306-309, stick/freebsd/platform.conf:8); only its comment (:2) says Linux [inferred: not yet run on a FreeBSD image]. F1 tests it and fixes the comment |
| bko-holt | `--only-disk works on Linux only` (holt/src/lib.rs:93-96); FreeBSD BEs give no backup sources (holt/src/zfs_be.rs:290-303); FreeBSD load writes no NVRAM entry (no efibootmgr call in other/load.rs; `other_boot`, :1014-1021) | `--only-disk` for FreeBSD (F4); BEs refused by BackShot1 (D9); NVRAM stays BackShot1's job, as above. Family F needs `keyed_by: both` restored for real (F11) |
| Bootstraps | `pika-bootstrap.sh` and the rest run on the Linux stick and read the drive (testvm/realworld/) | **new** `freebsd-bootstrap.sh`, `freebsd-bootstrap-qemu.sh` (D6) and `bsddialog-bs1`, a passphrase shim that reads a file at run time (below) |

The Mac side (`make-keys`, the ping sweep, `arp -an`, ssh and scp) doesn't
depend on the target's OS and doesn't change.

The rows above add up to est. 1.5-2.1k lines; key-access, the two
bootstraps, the shim and holt's change add about 0.5k more. The Linux code
paths must not change behaviour: each script's FreeBSD branch is taken only
on `uname -s` = FreeBSD, and F5 proves it with the 13
`testvm/test2/tests/test_bs1_*.py` files and two Linux rehearsals, one
systemd (`ubuntu26-uefi`) and one not (`alpine324-uefi`).

## The lane drive

- A USB drive the user supplies (Q1), one GPT partition, UFS2 with soft
  updates and journaling, `newfs -U -j -L BS1BSD` [label to be chosen], so
  it is `/dev/ufs/BS1BSD` on both the installed system and the stick.
  `DRIVE` runs `fsck -p` on it before each mount: it is mounted by hand, so
  rc's fsck never sees it, and an unclean UFS won't mount read-write
  [inferred].
- Formatted once, by `backshot1 format-lane SERIAL`, run on the FreeBSD
  stick, only after the user names the drive and agrees in chat. It
  refuses unless SERIAL is the only umass disk that isn't the stick, the
  disk holds no XFS and isn't mybig512 (D3), and it prints the drive's
  size and model for that yes. F6 runs it in QEMU first, against a USB
  disk image with a stand-in mybig512 attached.
- It holds what mybig512 holds for Linux, at the same paths:
  `backshot1/{bin,keys,runs}`, the scripts, `bs1.conf` (from
  `thinkpad-bsd.conf`). `bin/` has the FreeBSD builds:
  `holt-bin/freebsd/bko-holt` (cross-built against the freebsd14 sysroot,
  testvm/holttest1d/holt-cross.sh:10-12) and `bko-bin/freebsd/rustic-bko`
  (built on freebsd14, stick/freebsd/build-rustic.sh:17-22). Both run on
  15.1 and on GhostBSD.
- Also: `backshot1/freebsd/media/`, the verified memstick's pkgbase
  repository for the install (D6), and `backshot1/freebsd/tools/`, the tool
  root for the stick (D5).
- Staged before any FreeBSD install exists, so through the stick:
  `backshot1 stage --rescue TOOLS_DIR` sends the same files by tar over
  SSH to `backotter@` (mdo), behind D3's guard (`stage` today needs an
  installed system: backshot1:124-139).
- Mounted at `/mnt/bs1-backup` on both sides, as now. The stick's `/mnt` is
  a tmpfs (build.sh:284-293), so the mount point can be made. bsdinstall's
  new root defaults to `/mnt` [inferred: `BSDINSTALL_CHROOT`, and zfsboot's
  altroot], which would cover the drive mid-install: the same bug as
  d9c9f0e ("the target's mount on /mnt shadows" /mnt/bs1-backup) in
  guix-bootstrap.sh. So the bootstrap sets `BSDINSTALL_CHROOT=/mnt/bko-install`
  (*check* that zfsboot and scriptedpart honour it), and the rehearsal
  mounts its drive at `/mnt/bs1-backup` too, so it would catch a miss.
- The loader can read UFS [inferred: libsa], so family F can read its key
  from this drive (D7).
- Run dirs are BackShot1's own: `runs/RUN/` with its repository, layout,
  password, baseline, `PHASE1_OK`, `restores/STAMP/` (README, "Keeping a
  library of backups", :986 at d4aea22). RUN is `freebsd-15.1-STAMP` from
  os-release [inferred: FreeBSD 13+ has `/etc/os-release`; *check*].

## The FreeBSD rescue stick on the A100

- A second physical stick, written once with the newest FreeBSD rescue
  image built with key-access (F1), then given BackShot1's keys with
  `add-ssh-key.sh --host-key` (its BACKOTTER slice 3). After that the lane
  never writes to it. The Linux stick (192.168.68.64) and its contents are
  not touched by any of this.
- Written on the Mac by the user, or by me only after `diskutil info` on
  the `/dev/diskN` the user names shows an external, removable disk of the
  expected size whose serial and volume name aren't mybig512's, and a fresh
  chat yes for that write.
- **key-access** (D4), at boot, when slice 3 has `backotter/authorized_keys`:
  creates `backotter` if needed (as remote-access does,
  stick/bsd/remote-access:23-29), installs the keys for it, loads mac_do
  with a rule letting it act as root through `mdo` [inferred: FreeBSD 15
  base; *check* on the image], installs `backotter/ssh_host_ed25519_key` as
  the only host key if present (with `sshd_rsa_enable=NO` and
  `sshd_ecdsa_enable=NO`, or HostKey lines, since rc.d/sshd makes the
  missing types [inferred]), and starts sshd. Root gets no key: the
  image's `PermitRootLogin no` and `AllowUsers backotter` stand. Without
  the file, the image behaves as today: sshd off until the console's
  "remote access".
- The controller finds it as today, by MAC and ARP (backshot1:159-194),
  logs in as `backotter@` under the pinned alias `bs1-thinkpad`, and checks
  `/.disk/backotter.json` and the internal disk's serial by `diskinfo`.
  Both systems present the same host key and the same re0 MAC, so the
  user split is what keeps `phase1`, `adopt`, `verify` and `to-stick` off
  the stick if `BS1_HOST` is an address (Q5). On top, the FreeBSD branch's
  `host_up` and every `on_host` step test that `/.disk/backotter.json` is
  absent, and every `on_rescue` step that it is present.
- `kern.maxphys` stays the default. If the root doesn't mount over the
  A100's xHCI ("bad CSW signature", a `mountroot>` prompt), the fallback is
  `set kern.maxphys=65536` typed at the loader prompt (stick/freebsd/README.md,
  Notes), and then a loader.conf change in the image, and in the installed
  system's loader.conf by adopt, since the installed system writes the
  repository to the lane drive on the same xHCI. *Check on the A100* (F2
  reads the stick twice and compares; F3 round-trips a multi-GB file on the
  lane drive by SHA-256). Phase 1's and bs1-restore's `rustic check
  --read-data` (bs1-restore:107) catch corruption that slips past.
- **GELI at the rescue boot.** The stick's loader.efi probes every disk the
  firmware exposes, the NVMe included, so on a `zfs-geli` (GELIBOOT) disk
  it asks for that disk's passphrase before the stick boots, as the BIOS
  rescuer's gptzfsboot does in QEMU (geli2.md, "The BIOS rescuer asks too";
  holttest1d/README.md:459-461). An attended round so has two prompts: the
  stick's (typed, or skipped [inferred: libsa gives up after its tries])
  and the restored system's. The `zfs-bootpool-geli` provider has no
  GELIBOOT flag, so the loader leaves it alone; the stick's loader.conf
  loads no geom_eli (build.sh:315-328), so its kernel can't prompt at boot
  for the BOOT provider [inferred: GEOM_ELI isn't in GENERIC]. *Check* in
  F7: the stick boots over each GELI layout with console-boot.py logging
  but not answering, and family F fails on any prompt.
- The stick boots by UEFI from slice 1's `EFI/BOOT/bootx64.efi`
  (build.sh:396-404). The first boot is by the F12 menu. Later ones are by
  BootNext from to-stick, which first runs in OVMF (F7) and on the A100
  only in F8, after F2's read-only `efibootmgr -v` and BootOrder are saved
  on the Mac to put back. *Check on the A100* that FreeBSD's efibootmgr
  builds a usable device path for an MBR-sliced stick and that BootNext is
  honoured once with BootOrder unchanged.

## The install

`freebsd-bootstrap.sh SERIAL LAYOUT MEDIA_DIR KEYS_DIR HOSTNAME` in
testvm/realworld/, staged to `backshot1/freebsd/` on the lane drive and run
as root (mdo) on the stick over SSH, like `pika-bootstrap.sh`
(testvm/realworld/pika-bootstrap.sh:1-30):

- It takes the disk by serial only (`sysctl kern.disks`, `diskinfo -s`
  [*check* that it prints the ident on nda0]), and refuses a umass disk, a
  mounted disk, and runs only behind D3's guard.
- It runs detached: `daemon -o LOG` around `script -q /dev/null` with
  `TERM` set (bsdinstall's bsddialog gauges want a terminal [inferred]),
  writing an exit file, which the controller polls as phase2 does
  (backshot1:201-212). A dropped SSH session then doesn't kill the install
  halfway.
- It writes an `installerconfig` from the guests' one
  (testvm/freebsd/installerconfig) with `ZFSBOOT_DISKS` or `PARTITIONS`
  naming that disk, `nonInteractive=YES`, `COMPONENTS=base`,
  `BSDINSTALL_CHROOT=/mnt/bko-install` (the lane drive) and
  `BSDINSTALL_PKG_REPOS_DIR` pointing at a repo conf for
  `file:///mnt/bs1-backup/backshot1/freebsd/media/...` (the guests use the
  media's own, installerconfig:61-64). It keeps the guests' `bsdinstall()`
  wrapper for 15.1's ZFS `umount -F` failure (:30-43).
- The setup part, chrooted: `sysrc hostname=thinkpad ifconfig_DEFAULT=DHCP
  sshd_enable=YES`, root's `authorized_keys`, the fixed host key,
  `PermitRootLogin prohibit-password`. Root's password stays locked. The
  keys come from `KEYS_DIR`, copied into the stick's `/tmp` before the
  install starts and from there into the chroot's `/tmp`, removed after.
- It runs `bsdinstall script`, prints `BKO-FREEBSD-INSTALL-OK`, and sets
  BootNext to the "FreeBSD" entry bootconfig made [inferred: bootconfig
  makes one when the installer booted by UEFI; *check*].
- *Check:* that `/usr/libexec/bsdinstall` is still on the image (build.sh:280
  removes only `etc/rc.local`, `usr/freebsd-dist` and
  `usr/freebsd-packages`), and that pkgbase's install can bootstrap `pkg`
  offline from the staged repo (pkgbase.in runs `pkg bootstrap` when pkg is
  missing [read in the survey, not here]).

Layouts:

| LAYOUT | installerconfig | What it makes (15.1 zfsboot, from the survey of its source) | Unlock |
|---|---|---|---|
| `zfs` | `ZFSBOOT_BOOT_TYPE=UEFI`, GPT, stripe, swap 8g, no encryption | ESP at `/boot/efi`, swap, `zroot` with BE `zroot/ROOT/default` | none |
| `ufs` | `PARTITIONS="DISK GPT { 8G freebsd-swap, auto freebsd-ufs / }"` | scriptedpart adds the ESP on a UEFI-booted installer [inferred: bsdinstall.8] | none |
| `zfs-geli` | `zfs` plus `ZFSBOOT_GELI_ENCRYPTION=1`, `ZFSBOOT_SWAP_ENCRYPTION=1`; the passphrase through a new shim, `bsddialog-bs1`, which `cat`s a mode-600 copy of `KEYS_DIR/disk-passphrase` in the stick's `/tmp` (the guests' testvm/freebsd/bsddialog-geli:9 prints a passphrase make-freebsd.sh bakes in at build time, testvm/make-freebsd.sh:180) | `geli init -bg -e AES-XTS -J - -l 256 -s 4096` (GELIBOOT) on UEFI, as `freebsd15-geli` (geli.md, "What the guest is"), with onetime swap as `freebsd15-geli-bios` has (make-freebsd.sh:54-58): UEFI + GELIBOOT + onetime swap is a combination no guest has yet, so F6/F7 cover it | attended: the loader asks |
| `zfs-bootpool-geli` (F11) | `zfs-geli` plus `ZFSBOOT_BOOT_POOL=1 ZFSBOOT_BOOT_POOL_SIZE=2g` | `geli init -b` (no `-g`) with passphrase and `-K /boot/encryption.key`; a plain `bootpool` for `/boot`; loader.conf `geli_PROV_keyfile0_*` | family F (D7) |

UFS on GELI (geli-ufs.sh's layout) is out of scope for the A100 (QEMU
covers it, geli2.md).

## GELI unlock

How FreeBSD unlocks, from the survey's reading of 15.0 and 15.1 source
(not read again here):

- loader.efi tastes only GELIBOOT providers, has no key-file support, and
  asks for the passphrase when `devinit()` probes the disks, before it reads
  anything from the ESP or `/boot` (`loader.env` and `local.lua` come
  later). This is the NVMe's loader and the stick's alike.
- The kernel attaches BOOT providers at taste time: first with keys the
  loader passed, then with key files the loader preloaded, then with the
  passphrase cached from the loader's `kern.geom.eli.passphrase`, and only
  then by prompting.
- So the GELIBOOT layout (what the installer makes) can't be answered from
  anywhere: someone types at the loader. A layout whose `/boot` is plain
  can be: the loader runs `local.lua`, which can read a file from any disk
  the firmware exposes and set `kern.geom.eli.passphrase` for the kernel.

The options, with mybig512 ruled out (XFS: neither the loader nor the
kernel reads it, and it can't be reformatted):

| # | Where the passphrase comes from | Hands-free | Notes |
|---|---|---|---|
| 1 | typed at the loader (GELIBOOT, the installer's default) | no | what users get; like Linux family A |
| 2 | `local.lua` reads `backshot1/keys/disk-passphrase` from the **lane drive** (UFS), on a boot-pool layout; the kernel prompts if it's absent or wrong | yes, falls back to typing | the analogue of Linux's "key on the backup drive" (bs1-luks-unattended.md); the drive is plugged in for every run anyway |
| 3 | the same, from the FreeBSD stick's BACKOTTER slice (FAT16) | yes | keeps every secret on the stick, but the stick must be rewritten when the passphrase changes, and the drive already holds it for load's `--key-file` |
| 4 | a dedicated small key stick | yes | one more USB port, one more device for the user |
| 5 | key file or `kern.geom.eli.passphrase` in the plain `/boot` alone | yes | no secret at all, and no drive-absent prompt to test |
| 6 | an mfsroot that waits for USB, attaches, and reroots | yes | heavy, far from how FreeBSD boots |
| 7 | a patched loader.efi | yes | touches the boot loader; rejected |

`local.lua` (family F), as the survey sketched it [inferred API, from the
loader's config.lua: `io.open`, `io.read`, `loader.setenv`]: for each
`diskNpM:` the loader lists, open `/backshot1/keys/disk-passphrase` and a
marker file beside it that names the lane drive; on the first match set
`kern.geom.eli.passphrase` and `bs1.unlock=drive:diskNpM`; otherwise set
`bs1.unlock=none` and let the kernel prompt. Verify reads `kenv bs1.unlock`,
as the Linux families read their marker. `local.lua` and
`/etc/backshot1/geli-unlock` live in the installed system, so they come back
with the restore. *Check:* that Lua's `goto` and a length-less `io.read`
work in the 15.1 loader (else a flag and a size), that the cached
passphrase combines with the preloaded key file for a `-P`-less `-K`
provider, and *check on the A100* that the firmware exposes the USB drive
to loader.efi when booting the NVMe (`lsdev` at the `OK` prompt, in F2).
The evidence so far is good: family G's GRUB, a UEFI application booted
from the A100's NVMe, found the USB drive by label (bs1-luks:2292,
`search --no-floppy --label`) and unlocked hands-free on 2026-11-26
[from the session notes; the repo records the procedure, README "Guix
System from the stick", not that PASS]. If the firmware doesn't expose it,
option 3 (the stick's slice) or family F in QEMU only.

## The rehearsal

- `freebsd-bootstrap-qemu.sh [LAYOUT] TEST_IMG MEDIA_DIR WORK_DIR [PORT]`,
  modelled on pika-bootstrap-qemu.sh (:1-40): the FreeBSD `--test` image
  (stick/make-img-bsd.sh:7), with F1's keys, on EHCI, a blank emulated NVMe
  with a test serial, a lane drive (below) mounted at `/mnt/bs1-backup` as
  on the A100; `format-lane` and `stage --rescue` run first, with a
  stand-in mybig512 attached (a usb-storage disk of NEVER_SERIAL whose
  first partition holds an XFS superblock), which both must refuse; then
  the install, detached; then the disk alone boots from NVRAM and answers
  SSH as `thinkpad` with the fixed host key.
- The lane drive: Linux can't write UFS (testvm/make-freebsd.sh:43-44), and
  bs1-qemu.py makes its drive on the host with sfdisk and `mkfs.ext4`
  (:371-375). So the drive is a blank raw image given to QEMU as a
  usb-storage device with a serial, formatted by `format-lane` inside the
  stick VM and filled over SSH; that exercises umass, the serial
  allow-list and the refusals. Fingerprints for "`--plan-only` changes
  nothing" and the refusals are the raw images' SHA-256 with the VM
  stopped, or `gpart backup` + `sha256` inside the VM, not sfdisk/blkid.
- `bs1-qemu-bsd.py TEST_IMG WORK_DIR` then runs the cycle the A100 will:
  source boot (adopt, preflight, phase 1), to-stick (efibootmgr BootNext in
  OVMF's shared vars), rescue boot (bs1-restore: wrong serial refused,
  `--plan-only` changes nothing, the restore), restored boot (bs1-verify),
  with the stand-in mybig512 attached once to prove every step refuses it.
  GELI: the loader's prompt is answered on the serial console by
  testvm/console-boot.py, which already matches `GELI Passphrase for
  disk0p3:` (geli.md), and every prompt is logged and counted, so the
  stick's own prompt over a GELIBOOT disk is seen, not hidden. Family F
  boots with the drive (nothing typed, and any prompt fails the run),
  without it (the prompt comes and is answered), and the restored system
  with the drive. Its boots use family G's bootindex arrangement (README,
  "Rehearsal", the family G paragraph): OVMF connects only the devices
  QEMU's bootorder names, so the drive gets the last bootindex, or the
  loader would read it in QEMU even where real firmware wouldn't.
- Where: any amd64 farm host, from
  `H=$(testvm/pick-host --reserve 12 6)` (est. peak: the NVMe, the drive
  with the repo, the stick's copy), on H in /mnt/bko-ram under
  `cmp-run.sh --reserve 12 6` with `WEAR_TAG=bs1:freebsd15` and a `--port`
  of its own (backshot1/README.md, "Rehearsal"). The test image and the
  memstick media come from Chopin (`/bkoscratch/work/images/freebsd/`,
  `~/backotter-work/img-media/freebsd/`), copied host to host with their
  SHA-256 compared. No cached guest is needed: the rehearsal installs its
  own.

## Decisions

- **D1. Order and scope.** FreeBSD 15.1 on the A100, by UEFI: `zfs` first
  (the installer's default without encryption, and the riskiest piece: BEs
  and pools), then `ufs`, then `zfs-geli` attended, then family F.
  GhostBSD 26 after every FreeBSD layout passes. *Recommend:* yes. Or UFS
  first, the simplest stack, to shake out the lane's plumbing alone.
- **D2. Where the repository lives.** A second USB drive, UFS2, labelled
  for the lane, holding `backshot1/` as mybig512 does (*recommended*: the
  lane stays offline and self-contained as BackShot1 is; the scripts keep
  `-r RUN/repo` (bs1-common:600-602) and load's `--repo`; the loader can
  read it for family F; the A100's re(4) carries only SSH, not the
  backup). Alternatives:
  - ZFS on that drive (read-write on FreeBSD and on Linux too), at the
    cost of a second pool next to the one load builds, and an import on the
    stick;
  - REST over the LAN: `rest-server` on a farm host, `-r rest:http://...`
    on both sides (rustic-bko's FreeBSD build has the rest backend by
    default features [inferred from `strings`]); no extra drive, but a
    network path through re(4) for every byte, a server to keep up on the
    farm, and run dirs moved by the controller;
  - FUSE XFS (fusefs-lkl) on mybig512: rejected, a write path through an
    old embedded Linux kernel on the user's only backup drive.
- **D3. mybig512 during the lane.** Unplugged, and every FreeBSD step
  (format-lane, stage, bootstrap, adopt, preflight, phase 1, restore,
  verify) runs a positive guard (*recommended*): it acts only on disks
  whose FreeBSD ident is `INTERNAL_SERIAL`, `LANE_SERIAL` or
  `STICK_SERIAL` (all read on FreeBSD by `diskinfo -v`), and stops if any
  other disk is present, or if any disk or partition reads XFS
  (`XFSB` in its first sector, read-only) or carries one of mybig512's
  GPT partition GUIDs (recorded read-only from the Linux side, `lsblk -o
  PARTUUID`, at the next Linux block). The NEVER_SERIAL match is a last
  line, not the guard: 012345678921 is what Linux reports for the USB
  bridge, and FreeBSD's da(4) ident comes from SCSI inquiry and may differ
  or be empty [inferred], and the Linux lane itself finds mybig512 by
  label (bs1-common:73-80). bs1-restore's "exactly three disks" guard
  (bs1-restore:97-102) stays. Or keep mybig512 plugged in as a known
  bystander, allowed by its FreeBSD ident in both lanes' guards, which
  saves a swap at each block boundary but puts it in reach of FreeBSD's
  gpart, and needs its FreeBSD ident read first (Q8).
- **D4. The stick's way in.** key-access for the FreeBSD image, keys from
  slice 3, for `backotter` by key, root through base's `mdo` (mac_do)
  [inferred; *check* on the image] (*recommended*: the driver's split,
  `root@` = installed system and `backotter@` = stick (backshot1:54-60,
  :71-75), holds, and it changes nothing for an image with no keys). Or
  doas in the tool root; or `root@` by key, which needs the marker test on
  every on_host and on_rescue call as the only separation.
- **D5. bash and python3 on the stick.** A tool root on the lane drive,
  `backshot1/freebsd/tools/`, holding pkg's bash and python3 installed
  with `pkg --rootdir` [*check* the option] for 15.1, used through
  `LD_LIBRARY_PATH`, bash by its full path from the driver, and PATH
  prepended by bs1-common-freebsd after bs1-common's own reset (:8)
  (*recommended*: the image stays package-free, build.sh:280, and
  bs1-common's bash ports once). *Check* in F7 that a relocated python3
  finds its standard library. Or rewrite bs1-restore's Python helpers in
  flua (as stick/freebsd/checks.sh reads JSON), ~200 lines more; or add
  both to the image, which is a user product.
- **D6. How FreeBSD gets onto the NVMe.** `bsdinstall script` run over SSH
  from the FreeBSD stick by `freebsd-bootstrap.sh`, detached, its new root
  at `/mnt/bko-install`, from the memstick's pkgbase repository on the lane
  drive (*recommended*: the installer's own layouts, offline and pinned to
  the verified memstick, and nothing wipes at boot). Rejected: a memstick
  that installs at boot (any boot of it wipes a disk), a pool and tarballs
  made from the Linux stick (no GELI, no FreeBSD flags, not what users
  get). By hand from the official memstick is the fallback if bsdinstall
  turns out to be gone from the image.
- **D7. GELI unlock.** Attended first (option 1, `zfs-geli`), then family F
  (option 2: `zfs-bootpool-geli` and a `local.lua` reading the lane drive)
  (*recommended*). Or option 3 (the stick's slice) if the firmware exposes
  the stick but not the drive. Unattended unlock never applies to the
  GELIBOOT layout.
- **D8. Script layout.** One driver and the same bs1-* names: a
  `bs1-common-freebsd` override file plus `uname -s` branches in the step
  scripts, and new files only where nothing is shared
  (`freebsd-bootstrap*.sh`, `bsddialog-bs1`, `bs1-qemu-bsd.py`,
  `stick/bsd/key-access`) (*recommended*: one place per step, and the ~35
  Linux runs' paths are untouched). Or separate `bs1-bsd-*` scripts,
  cleaner per file but duplicating the driver's logic.
- **D9. Inactive boot environments.** Preflight and phase 1 refuse a ZFS
  layout with a BE other than the active one in `bectl list`, naming it,
  as `unhinted_check` does on Linux (*recommended*: a fresh install has one
  BE, and holt doesn't back up FreeBSD BEs, holt/src/zfs_be.rs:290-303).
  `canmount=off` containers aren't BEs and aren't refused. No base or
  package upgrades on the installed system before a run (the install is
  pkgbase, installerconfig:61-62, so base updates through pkg, and an
  upgrade can make a BE [inferred]). Or extend holt's BE sources to
  FreeBSD's bectl first (a holt change of its own).
- **D10. BackShot1All.** FreeBSD rows join bs1-all.md under its Dropped rows
  rule (a row joins once its fresh install passes on the A100), as a block
  of their own in a sitting of their own: the user swaps the USB devices
  and boots the FreeBSD stick by F12 at the start, and the Linux stick at
  the end (*recommended*: D3's unplugged mybig512 means no automatic
  crossing between lanes). Attended GELI rows go first in the block, as
  README's "Attended runs go first, in one block" (:1308 at d4aea22) says
  for any series, and run step by step, not by `cycle`. Or both lanes'
  devices plugged in at once, which needs D3's bystander option and a
  cross-lane to-stick.

## Milestones

| | What | Done when | est. |
|---|---|---|---|
| F1 | `stick/bsd/key-access` (FreeBSD: `backotter`, mdo, the one host key), installed by build.sh as `/etc/rc.d/backotter_key_access`; `add-ssh-key.sh` run on the FreeBSD image (comment fixed, code only if the test fails); the key login case as `stick/key-login-test.py` (SSH-driven, so it runs on release images too; boot-test.py drives the serial console only). stick/bsd/* is in every VM platform's image digest (img-inputs.tsv), so the next image round builds all of them in full. Built 2026-11-27: mac_do(4) and `/usr/bin/mdo` are in 15.1 base, and the rule `uid=N>uid=0,gid=*,+gid=*` (sysctl `security.mac.do.rules`) lets `mdo id -u` print 0; `add-ssh-key.sh` worked unchanged on the MBR image | a `--test` and a release image boot in QEMU, sshd answers `backotter@` by key with the pinned host key, `mdo id -u` prints 0, root can't log in, and with no keys the image is as before | 0.75 d |
| F2 | First look at the A100, read-only: the user boots the F1 stick by F12; checks over SSH | `machdep.bootmethod` is UEFI; the root mounted over xHCI and two full reads of the stick's root slice give one SHA-256; `re0` shows 8168H; the idents of every disk by `diskinfo -v` (nda0 is S7XMNF0Y787118; the stick's becomes `STICK_SERIAL`); `efibootmgr -v` and BootOrder saved on the Mac; the SecureBoot variable read (`efivar` [inferred tool]); `lsdev` at the loader's `OK` prompt, with a USB drive in (Q2); nothing written to any disk or to NVRAM | 0.25 d + an hour of the user's |
| F3 | The lane drive and `thinkpad-bsd.conf` on the A100 (needs F6's QEMU run of `format-lane` and `stage --rescue`) | after the user's chat yes for that serial, the drive is `/dev/ufs/BS1BSD` on the stick, `backshot1/` staged, a multi-GB file written and read back with one SHA-256 and `re0` keeping its link through a multi-GB copy, and `format-lane`/`stage` refuse an unlisted disk | 0.5 d |
| F4 | bko-holt `dump --only-disk` on FreeBSD: filter every section other/freebsd.rs:388-458 builds (`disks`, `mounts`, `fstab`, `swaps`, `glabels`, `zfs`), so the lane drive's `/mnt/bs1-backup` mount and labels go too; unit tests; HoltTest1BSD `freebsd15` and `freebsd15-geli` on two pick-host hosts (High subagent) | the lib.rs:93-96 refusal is gone, a dump with the drive and stick attached names only the internal disk anywhere, and both HoltTest1 runs PASS | 0.75 d |
| F5 | `bs1-common-freebsd` and the FreeBSD branches of the driver (`DRIVE`, `stage --rescue`, `format-lane`, the marker tests), adopt, backup, restore, verify, manifest, to-stick, the luks guard (High subagents, one script each) | shellcheck clean; each script's FreeBSD branch runs on a freebsd15 guest by hand; the 13 `test_bs1_*.py` pass; `ubuntu26-uefi` and `alpine324-uefi` rehearsals still PASS | 2.5 d |
| F6 | `freebsd-bootstrap.sh`, `bsddialog-bs1` and `freebsd-bootstrap-qemu.sh`, layouts `zfs`, `ufs`, `zfs-geli` (needs F1: the QEMU bootstrap logs in to the `--test` image by key; and F5's `format-lane`/`stage --rescue`) | `format-lane` and `stage --rescue` refuse the stand-in mybig512 and format only the named disk; each layout installs detached in QEMU onto an emulated NVMe with the drive at `/mnt/bs1-backup`, and boots alone by its NVRAM entry, answering SSH as `thinkpad` | 1 d |
| F7 | `bs1-qemu-bsd.py`: the whole cycle, on a pick-host host | PASS for `zfs`, `ufs` and `zfs-geli` (typed by console-boot.py, prompts counted: two per round); to-stick's BootNext honoured once in OVMF with BootOrder unchanged and no entry growth over 3 rounds; the stand-in mybig512 refused by every step; the FreeBSD churn list settled | 1.25 d |
| F8 | The A100, `zfs`: confirm `backshot1 list` (on the Linux lane) shows a PASSed backup of the current install first; bootstrap, adopt, preflight, phase 1, `cycle 1` (its first to-stick is the first NVRAM write on the A100, with F2's saved BootOrder to put back) | RESULT PASS, the restored system booted by the restored ESP's entry, BootOrder as saved | 0.5 d |
| F9 | The A100, `ufs` | as F8 | 0.25 d |
| F10 | The A100, `zfs-geli`, attended, step by step: to-stick, plan, phase2, reboot, wait-host, verify | RESULT PASS with the user typing at each prompt (the stick's and the restored system's); `UNLOCK=attended` in unlock.txt | 0.5 d |
| F11 | Family F: `make-freebsd.sh --bootpool-geli` guest and HoltTest1 of its `keyed_by: both` restore (geli.md load step 5); the bs1-luks step and `local.lua`; F7 for `zfs-bootpool-geli` with the bootindex arrangement; then the A100, gated on F2's `lsdev` | HoltTest1 PASS on two hosts; the rehearsal's three boots as D7 says; on the A100, a cycle with nothing typed and `kenv bs1.unlock` showing the drive | 1.5 d |
| F12 | Docs: README's FreeBSD section, bs1-all.md's FreeBSD block and rows, geli.md D7's pointer | the rows are in bs1-all.md with their estimates, and the README says how to run the lane | 0.25 d |
| F13 | GhostBSD 26 (below) | a GhostBSD `zfs` cycle PASS on the A100 | 1.5 d |

**F4 done 2026-11-27 (b7e5db3).** `other::freebsd::report_only` still
reads the disks it leaves out (to tell which mounts are theirs), then
drops their entries, GEOM labels, mounts, fstab entries and swaps, every
mount below a dropped one and every nullfs mount from there, and the ZFS
pools only they hold (label pools, zpool.cache, imported pools,
datasets); `/` is never dropped. `--only-disk` names are checked against
`kern.disks` (`report::unknown_freebsd_disk`); NetBSD, OpenBSD, DragonFly,
illumos and Haiku still refuse it. `boot.efibootmgr` is kept as
`efibootmgr -v` prints it (NVRAM is the firmware's, as on Linux), so a
UEFI dump can still name the stick in a Boot entry's device path. Live
check on chopin: `freebsd15` with the lane drive (vtbd1, UFS at
`/mnt/bs1-backup`, tmpfs below, nullfs from it) and a USB stick (da0,
mounted): the plain dump names both, `--only-disk vtbd0` neither.

**F5 part 1 done 2026-11-27: bs1-common-freebsd and the driver.**
bs1-common sources `bs1-common-freebsd` at its end when bash's `$OSTYPE` is
freebsd* (no `uname` process on Linux). It puts the tool root
(`BS1_TOOLS`, else `HERE/freebsd/tools` on the stick) first on PATH with
`LD_LIBRARY_PATH`, and redefines the functions the table lists. Three
things differ from the table:

- `hold_periodic` runs through `hold_awake`, which bs1-backup and
  bs1-verify already call. cron stops, and a `daemon -f` child starts it
  again once the script exits, so the step scripts need no restart call.
- `check_coverage` stops on an unattached GELI partition, a ZFS pool that
  isn't imported, and any unmounted dataset over 1 MiB that isn't
  `canmount=off` (an inactive BE among them). Smaller unmounted ones
  (zroot/ROOT, mountpoint none) pass.
- `unlock_mode` reports GELIBOOT as attended, a provider keyed only by key
  files as own, and family F as drive only with `kenv bs1.unlock=drive*`
  and `/etc/backshot1/geli-unlock` present (F11 finishes it).

The driver takes `BS1_CONF` (a name beside it or a path), and
`thinkpad-bsd.conf` sets `LANE_OS=freebsd`. With the default conf every
command string the driver sends is unchanged (checked with ssh faked:
identical). On FreeBSD:

- `on_host`/`on_rescue` prefix the stick-marker test, and exit 97 says
  which side it was.
- `DRIVE` reuses an existing mount (by `mount -p`), else runs `fsck -p`
  (output to stderr) and `mount -t ufs` at /mnt/bs1-backup.
- bash is called by its full path; `stage` (on the installed system) and
  `stage --rescue` send one tar stream behind the guard (`TOOLS_DIR/freebsd/`
  becomes `backshot1/freebsd/`).

D3's guard and the format are a new POSIX sh file, `bs1-lane` (`guard
[--lane]`, `format SERIAL [--yes]`). The driver sends it with the conf
ahead of it on stdin, so it runs before anything is staged.

- `format-lane SERIAL` refuses unless SERIAL is the conf's `LANE_SERIAL`.
  Without `--yes` it only shows the disk (size, descr, ident, attachment);
  with `--yes` it runs gpart destroy, a 4 MiB dd, a GPT partition labelled
  `bs1lane`, and `newfs -j -L BS1BSD`.
- Optical drives (cdN) are skipped by the guard.

Settled on a freebsd15 guest (chopin, `--ram`):

- `geli attach -C` works on an attached provider (right key 0, wrong key
  refused).
- nullfs of / doesn't show the mounts below it.
- QEMU usb-storage's `serial=` is da(4)'s ident, padded with blanks.
- A 1 MiB write over QEMU's xHCI hung for over 4 minutes, so the
  rehearsal's lane drive must be on EHCI, as the stick is
  (stick/freebsd/README.md).

**F5 part 2: bs1-adopt and bs1-backup.** Both branch on bash's `$OSTYPE`
(bs1-adopt's sh prelude on `uname -s`); Linux runs nothing new.

- bs1-adopt: with no bash, the sh prelude runs `pkg install -y bash`
  (ASSUME_ALWAYS_YES) and execs `/usr/local/bin/bash` by its full path. Then
  the lane guard (`bs1-lane guard --lane`) before anything changes; init
  `bsd`: `sysrc hostname` and `hostname`, `sysrc NAME_enable=YES` (dashes as
  underscores) and `service NAME restart`, `ps -axo comm` for `running`,
  pkg's python3 and avahi-app (dbus comes with it), `sshd_rsa_enable=NO` and
  `sshd_ecdsa_enable=NO` (rc.d/sshd makes every enabled type that is
  missing) in place of `mkdir -p /run/sshd`, and on a UEFI boot
  (`machdep.bootmethod`) `/boot/efi` must be in fstab and mounted. An
  enabled pf or ipfw is named, not changed (F13 settles GhostBSD's). No
  sleep drop-in: nothing suspends a base system by itself.
- bs1-backup: the guard first in both the preflight and phase 1, then
  `bsd_be_check` (D9: on a ZFS root any BE `bectl list` shows without `N`,
  named, inactive or activated for the next boot) before the coverage check;
  `unhinted_check` passes on FreeBSD (its dump has no backup_sources). The
  drive's key is tested against every GELI provider that asks
  (`geli_key_test`). The baseline has `gpart.txt` (`gpart show -lp`, `gpart
  list DISK`) and `gpart-backup.txt` (`gpart backup DISK`) in place of
  lsblk.txt and sfdisk.txt (bs1-verify reads neither), and the log has
  `gpart show -lp` and the tree's `mount -p` lines.
- Base 15.1 has `sha256sum`; `/etc/os-release` gives `freebsd-15.1`.

F1 and F4 are independent and can run side by side; F2 needs F1; F5 needs
F4; F6 needs F1 and F5; F7 needs F6; F3 needs F6; F8 needs F2, F3 and F7.
Nothing writes to the A100's disks, NVRAM or a real USB drive before F3,
and nothing to NVRAM before F8. Every source change goes through a
High-effort subagent with tests, and is committed on main when the user
asks. Total 11.5 d (est.).

## GhostBSD, the follow-on

- bko-holt already takes it (`ghostbsd` 26, holt/src/osrel.rs:279; the dump
  is FreeBSD's) and the FreeBSD stick restores it (GhostBSD 26 is 15.1
  based, layload.md:969).
- Its installer is pc-sysinstall, not bsdinstall. `make-ghostbsd.sh` drives
  it from the live system's serial login (testvm/make-ghostbsd.sh:1-11),
  and its guests are BIOS (`efiLoader=none`, testvm/ghostbsd/pcinstall.cfg:24).
  The A100 needs a UEFI install, so a `ghostbsd-bootstrap.sh` runs the ISO's
  pc-sysinstall from the FreeBSD stick (the ISO on the lane drive, its
  image mounted, a chroot as pika-bootstrap.sh uses the PikaOS live image)
  with a UEFI config [*check* pc-sysinstall's UEFI option], and keeps the
  desktop (what users have).
- Expected extra work [inferred]: holds for its desktop's power manager
  (suspend) and update notifier beside `hold_periodic`, its firewall,
  drm-kmod's i915kms loading by default on Alder Lake-N (hangs reported;
  `kld_list` override ready), its own pkg repo for adopt, an automount of
  the lane drive (which `DRIVE` reuses), and its rc setup (*check* whether
  GhostBSD 26 is on rc.d or OpenRC).

## Risks

- **mybig512.** If it were plugged in by mistake, D3's allow-list, the XFS
  and GUID checks, the NEVER_SERIAL stop and the three-disk guard all
  refuse. The lane writes only to the internal disk and the lane drive,
  each by FreeBSD serial. The stick is written on the Mac only behind
  `diskutil info` and a chat yes.
- **The stick on real xHCI.** Never tested. Mitigation: F2's read-only
  checks first, F3's checksum round trip on the lane drive, the
  `kern.maxphys` fallback in both loader.confs, and `--read-data` checks.
- **re(4) under load.** Watchdog timeouts are reported for RTL8168 parts.
  With D2's local drive only SSH crosses it; the fallbacks are
  `hw.re.msix_disable`, a USB NIC (ure(4)), or realtek-re-kmod on the
  installed system.
- **efibootmgr on FreeBSD.** Its output and the device path it builds for an
  MBR-sliced stick are unverified. F7 runs to-stick in OVMF before F8 runs
  it on the A100, with F2's saved BootOrder to restore.
- **The restored boot entry.** holt writes no NVRAM entry for FreeBSD, but
  restores the partition GUIDs exactly (other/load.rs:697, 774), so the
  installer's "FreeBSD" entry should still match the restored ESP
  [inferred]; if NVRAM lost it, bs1-restore's one-shot "BKO restored"
  entry stands in. bs1-restore sets BootNext and verify checks
  BootCurrent; an ESP left unmounted on the source would come back empty,
  and adopt must check that `/boot/efi` is in fstab and mounted.
- **A live backup of a running FreeBSD root** has never been done with
  rustic-bko (HoltTest1BSD backs up offline, ht1-bsd.sh:12-17). Churn and
  flags come out in F7; cron (periodic, save-entropy, newsyslog) is held
  for phase 1 and verify, since overnight BackShot1All rows cross 03:01.
- **Silent loss of a BE**, if D9's check misses one. F7 includes a second BE
  made on purpose.
- **A dropped SSH session mid-install** (as on PikaOS LUKS, 2026-11-27).
  The bootstrap runs detached, as bs1-restore does.
- **A dirty lane drive** after a hang or power cut. `DRIVE` runs `fsck -p`
  first; SU+J keeps that short.
- **Linux regressions** from shared scripts. D8's branches, the 13 unit test
  files and F5's two Linux rehearsals.
- **The user's time** on attended GELI rows: two typed passphrases per
  round (the stick's loader over the GELIBOOT disk, and the restored
  boot), on top of A's and B's.
- **Secure Boot** comes back on after a BIOS reset; the stick then doesn't
  boot. F2 reads the SecureBoot variable, and each block's start reads it
  again.

## Out of scope

- Reformatting, repartitioning or mounting mybig512 from FreeBSD in any way;
  moving the Linux library off XFS.
- Any change to the Linux rescue stick or its contents.
- NetBSD, OpenBSD, DragonFly, HardenedBSD, illumos and Haiku on the A100
  (key-access is written for FreeBSD first; the other BSD images can take
  it later).
- BIOS/CSM boot of the A100, Wi-Fi, graphics beyond the efifb console.
- UFS on GELI on the A100 (QEMU covers it), GELI with authentication or a
  second key slot on the A100.
- FreeBSD boot environments as backup sources (D9's alternative), and OS
  updates on the installed system before a run.
- BackShot2 on FreeBSD; restic-bko on the lane (BackShot1 uses rustic-bko).
- A network repository (D2's alternative), unless the user picks it.

## What the user must do

- **Hardware.** Supply a second USB stick for the FreeBSD rescue image, and
  a USB drive for the lane (Q1), and say in chat which drive may be
  formatted (UFS2; its FreeBSD serial, size and model are read back to you
  before anything is written).
- **Write the stick.** Write the image to the stick from the Mac, or let
  me: name the Mac's `/dev/diskN`, I show its `diskutil info` (external,
  removable, size, not mybig512), and you say yes for that one write. Then
  I run `add-ssh-key.sh --host-key` on it once.
- **The passphrase file.** Put the GELI passphrase in
  `backshot1/keys/disk-passphrase` on the lane drive (`printf '%s'`, no
  final newline), as for mybig512. The scripts read it at run time; it is
  never on a command line.
- **BIOS.** Check that Secure Boot is off, and leave USB boot enabled. If
  family F is wanted, any "fast boot" setting that skips USB at boot must
  be off (*check on the A100*).
- **At each FreeBSD block** (F2, F3, F8-F11, and BackShot1All's block):
  unplug mybig512 and the Linux stick, plug in the FreeBSD stick and the
  lane drive, and boot the stick by F12. At the end, swap back and boot the
  Linux stick by F12.
- **Type** the GELI passphrase on the A100's screen at every boot of an
  attended row (`zfs-geli`) that asks: the stick's loader over the
  encrypted disk, and the restored system's; and at the drive-absent boot
  of family F's A100 run. A push notification says when.
- If the stick's root doesn't mount (F2): type `set kern.maxphys=65536` and
  `boot` at the loader prompt.

## Open questions

- **Q1.** Which drive for the lane, and its label? Anything of 128 GB or
  more will do (est.: a FreeBSD base system's repo is a few GiB per run).
- **Q2.** Does the A100's firmware expose USB storage to loader.efi when it
  boots the NVMe? Family F's A100 run depends on it (F2 checks; family G's
  GRUB on the same firmware suggests yes).
- **Q3.** Phase 1 from the live tree (parity with Linux), or from a
  recursive ZFS snapshot (consistent, no churn) on ZFS layouts?
- **Q4.** What does rustic-bko record on FreeBSD (flags, extattrs, NFSv4
  ACLs on ZFS: rustic_core's osattrs/freebsd.rs and bsd_flags.rs), and
  what must the manifest compare to match it?
- **Q5.** mDNS: avahi from pkg in adopt so `thinkpad.local` resolves, or the
  driver finding the installed system by MAC as find-rescue does
  (`BS1_HOST`)? The latter leans on D4's user split and the marker tests,
  since the stick has the same MAC and host key.
- **Q6.** Should family F wait for F10's attended GELI PASS, or be built in
  parallel once F7 passes?
- **Q7.** GhostBSD: keep the MATE desktop on the A100 (what users have, with
  drm-kmod's risk), or remove it as the guests do?
- **Q8.** May mybig512 ever be plugged into the FreeBSD stick for a
  read-only `diskinfo -v`, to learn its FreeBSD ident? *Recommend:* no; D3's
  allow-list doesn't need it, and only D3's bystander option would.
