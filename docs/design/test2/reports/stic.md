# SticTest1 as implemented: evidence for SticTest2 (researcher: stic)

**Sources.** The repo on main. Both hosts' `~/backotter-work/stictest1/*/logs/*/run.out*`: 201 runs, parsed into `/private/tmp/claude-501/-Users-mchobbit-git-backotter/6e7b60cc-f873-4450-892c-1b053e7a5606/scratchpad/t2/stic/runs.txt`. Round rel4 (2026-11-06/07): 41 guests, 1,168 PASS, 154 LIMIT, 0 FAIL. Its report is `/private/tmp/claude-501/-Users-mchobbit-git-backotter/b43d6ef3-8bbc-4dc4-9768-d915a92c69e4/scratchpad/rel4st1/REPORT.md`.

## (a) Per-guest pipeline

There is no shared framework: 41 hand-written guest directories, 18,413 lines in all. Each `check.sh` follows the same skeleton:
1. **Slot and room.** It takes `slot_take 3` on the host's port range (`compare/guest-slot.sh:39-57`), then `cmp-run.sh --reserve DISK MEM` (GiB of RAM disk, GiB of memory). Since 2026-11-07 that runs under iolog.sh (`cmp-run.sh:200-223`).
2. **Throwaway guest.** `vm_boot --snapshot` with TMPDIR=/mnt/bko-ram/PREFIX puts QEMU's snapshot file in the 30 GiB tmpfs (`vm-run:26-30,93-95`). `--ram` puts the overlay in RAM_DIR, which defaults to /dev/shm (`vm-run:31-35,92,160`).
3. **Test.** Copy the binaries in, build the fixture on fresh scratch file systems made with the guest's own mkfs, and back up with both tools. Compare the snapshots node by node (`rt_nodes`: one `restic cat blob` plus awk per tree, `rt-common.sh:262-290`). Then the restores and the judge. Logs go to `~/backotter-work/stictest1/GUEST/logs/MARK`, which is on /bkoscratch.

| Family | Boots | Reserve (GiB disk/mem) | Scratch FS (sparse) | Backups / restores (fresh+over+non-root) | Written to |
|---|---|---|---|---|---|
| debian13, devuan6, ubuntu26-uefi, debian13-stack (older form: debian12, nixos2605) | 1 | 3-4/5 (stack 7/5) | Source and each of 4 targets: 3G root-like + 512M ext4 /boot-like + 256M ext4 project+casefold. 3G non-root. torture.sh's random file cut from 1 GiB to 8 MiB (`debian13/inner.sh:22-34,56`) | 6 / 4+4+4, and each tool `check`s the other's repo (`:185-303`) | Guest /var/tmp, which is the overlay in /mnt/bko-ram. Stack: 2 spare qcow2 there, 6.1 GiB peak measured (`ramdisk-peak-kib`) |
| fedora44, fedora44-uefi, rocky10, rocky10-lvm, tumbleweed | 1 | 2-4/6 | 16G spare qcow2; 1500M loop images per FS | Per FS 2 / 4+2 (each tool over its own restore), plus nonroot-inner.sh (2/4). fedora44 total 6/16 (`fedora44/inner.sh:30-86`) | /mnt/bko-ram (rocky10-lvm used 1,089 MiB) |
| mint22, mx25, pop24, zorin18 | 1 | 2/4 (pop24 3/6) | 3-4 FS types (FSLIST). Each 1500M image sits in a 1500M tmpfs inside the guest; repositories in a 1G guest tmpfs (`mint22/inner.sh:18,24-25`) | Per FS 6 / 4+4+2 (non-root same-tool only). mint22 total 24/40 | Guest RAM |
| ubuntu24, ubuntu26-zfs, debian13-zfs | 1 | 13/6 (13/5) | fs_roundtrip.py ×3 (zfs-ds, zfs on file pools, ext4) with 4G images and the **full** torture.sh, 1 GiB random (`torture.sh:74`) | Per FS 4 fresh repositories (`compare.py:1187`) and 4+4+4 (fresh, over, rerun; `fs_roundtrip.py:654-705`), then real.sh and non-root. ≈16/≈48 | Overlay in /mnt/bko-ram, 11.6-12.0 GiB (commit bd2a178) |
| ubuntu26-zfsmirror | 1 (--ram) | 9/4.5 | zfs-ds, then ext4 + nonroot-rt.sh in parallel | ≈10/≈28 | Overlays 7.67 GiB in RAM (b7850da); **24G spare on /bkoscratch** holding repositories of ~1.1 GiB each (`check.sh:13-15,63-64`) |
| freebsd14, freebsd15, freebsd15-geli, ghostbsd26-zfs | 1 | 3/6 | roundtrip.sh ×4 on a ZFS dataset plus a 2g swap-backed UFS md (nfsv4acls); extra.sh on a ZFS dataset | ≈10/≈24 (each direction: fresh, over, onto UFS without ACLs) | Overlay; the md disks in guest RAM |
| freebsd15-ufs, ghostbsd26, hardenedbsd15 | **6**, 3 at once | 3 × 3/6 | The same on the UFS root, plus a 3g md | as above | **/dev/shm**, which the ledger doesn't count |
| dragonfly6 | **5** in turn | 3/6 | HAMMER2 /root, UFS /boot | 4 directions + over + non-root 4 | /dev/shm |
| netbsd11, alpinebsd | 1 (alpinebsd: +1 netbsd11 boot when the host lacks NetBSD's tools) | 3/6 | FFSv2ea posix1e/nfs4 vnd images, FFSv1, plain FFSv2 | 4 directions × (fresh, over, other FFS), probe.sh, non-root | Overlay |
| openbsd8 / omnios58 | 1 / **2** | 3/6 | FFS2 vnd 2G / ZFS datasets + tmpfs | 4/4+4+4; omnios58 restores every repository again in its 2nd boot (fresh idmap) | Overlay |
| haiku6 | 1 | 3/6 | 2× 2G raw BFS | rustic only; the host's Linux restic-bko runs `check --read-data` and restores | /mnt/bko-ram |
| sculpt2610 | 0 | 2/2, no slot | On the host as root: raw copy of the disk, GENODE ext2 read-only, 512M ext2 targets | 2 / 4+1 | /mnt/bko-ram |
| arch, alpine324, artix-dinit, artix-s6, cachyos, omarchy, void-musl, aerynos | 1 | 1-3/5-6 | e.g. arch: ext4 512M + 7×256M, judged on the host with its own older fsmeta.py | 2 / 4, + over (arch 2 tools × 3), + non-root 2 | /mnt/bko-ram. **aerynos: overlay and 1.5G + 10G raw disks on /bkoscratch** (`aerynos/check.sh:22,30,37`) |

A full round is about 60 boots: 35 one-boot guests, omnios58 2, dragonfly6 5, the UFS three 18.

## (b) Coverage unit

- **Linux.** One binary pair per round is shared by all 27 Linux guests. The same pair also runs sculpt2610's host-side check, artix's host-side step, and haiku6's host-side restic read.
  - restic-bko is a static Go binary (CGO_ENABLED=0, restic `build-backotter.sh:52`).
  - rustic-bko is a static-pie musl build with mimalloc (`rustic/build-backotter.sh:62-67`; checked with `file` on `chopin:~/backotter-work/roundrel4/bin/linux`).
  - Both tools read /etc/passwd themselves, so the guest's NSS setup never reaches them.
- **FreeBSD.** One rustic-bko, built in freebsd14 since 22a5089, serves 7 guests. restic-bko is static, "for FreeBSD 12.3".
- **Other BSDs and illumos.** NetBSD's rustic-bko is built in netbsd11 (11.1). OpenBSD's and illumos's binaries are dynamic.

Where kernel versions come from: vms/GUEST/update-serial.log or update.log, guest.txt, and run logs. "n/r" means not recorded. "pc" means ext4 with project IDs and casefolding.

| Guest | OS, kernel | Root FS | Fixture FS | LSM / notes |
|---|---|---|---|---|
| aerynos | AerynOS, n/r | f2fs | f2fs | none; no python3 |
| alpine324 | Alpine 3.24 musl, 6.18.60-lts | XFS + ext4 | XFS, ext4 | none; apk installs tools every run |
| arch | Arch, 7.2.14-arch1 | ext4 | ext4 (root's features) | none |
| artix-dinit | Artix, 7.2.14-artix1 | JFS + ext4 | JFS, ext4 | none; + host-side nameless-owner test |
| artix-s6 | Artix, 7.2.14-artix1 | ext4 | ext4 | none |
| cachyos | CachyOS, 7.3.1 | JFS + ext4 | JFS, ext4 | n/r; perl judge |
| debian12 | 6.1.0-54 | ext4 | ext4 | AppArmor |
| debian13 | 6.12.115+deb13.1 | btrfs @rootfs | btrfs (subvolume, compression, nocow), ext4, pc | AppArmor |
| debian13-stack | 6.12.115 | ext4 on LVM/LUKS2/md1, XFS /srv | the same stack, 2× FAT32 | AppArmor |
| debian13-zfs | 6.12.115, OpenZFS 2.3.9 | ZFS xattr=on | zfs-ds, zfs, ext4 pc | AppArmor |
| devuan6 | 6.12.115 | ext3 | ext3, ext4, pc | AppArmor |
| fedora44 | 7.2.15-200 | btrfs zstd:1 | btrfs, ext4 | SELinux enforcing |
| fedora44-uefi | 7.2.15-200 | btrfs, XFS /boot, ESP | btrfs, XFS, vfat | SELinux enforcing |
| mint22 | 7.0.0-42 | F2FS | f2fs, f2fs with every feature, ext4, tmpfs | AppArmor |
| mx25 | 6.12.115 | ext2 | ext2, ext4, pc, tmpfs | AppArmor |
| nixos2605 | 6.18.60 | bcachefs, 512-byte blocks | bcachefs | none |
| omarchy | 7.2.8 | LUKS2 btrfs zstd:3, FAT32 /boot | btrfs, FAT32 | n/r |
| pop24 | 6.16 per its header; 7.2.15 also installed | ext4 + ESP | ext4, pc, tmpfs, vfat | AppArmor |
| rocky10 / rocky10-lvm | 6.12.0-211.el10 | XFS / XFS on LVM | XFS (+ XFS /boot) | SELinux enforcing |
| tumbleweed | 7.3.1-default | btrfs + snapper | btrfs, ext4 | SELinux enforcing |
| ubuntu24 | 6.8.0-149, OpenZFS 2.2.2 | ZFS xattr=sa | zfs-ds, zfs, ext4 pc | AppArmor |
| ubuntu26-uefi | 7.0.0-43 | ext4 + ESP | ext4, pc, vfat | AppArmor |
| ubuntu26-zfs | 7.0.0-43, OpenZFS 2.4.1 | ZFS bpool + rpool | zfs-ds, zfs, ext4 pc | AppArmor |
| ubuntu26-zfsmirror | 7.0.0-43, OpenZFS 2.4.1 | encrypted mirrored ZFS | zfs-ds, ext4 pc | n/r |
| void-musl | 6.18.60 | btrfs | btrfs, ext4 | none |
| zorin18 | 7.0.0-42 | NILFS2 | nilfs2, tmpfs, ext4 | n/r |
| sculpt2610 | Genode; runs on the host's 6.12.115 | ext2 | ext2, offline | — |
| freebsd14 / freebsd15 / freebsd15-geli / ghostbsd26-zfs | 14.5 / 15.1 / 15.1 / 15.1 | ZFS (GELI under the pool for -geli) | ZFS + UFS md nfsv4 + UFS without ACLs | MAC off: file flags, DOS flags, NFSv4 ACLs, extattrs, birth times |
| freebsd15-ufs / hardenedbsd15 / ghostbsd26 | 15.1 / 15.1-STABLE-HBSD / 15.1 | UFS2 SU+J / SU+J / SU + TRIM | UFS POSIX.1e + md nfsv4 | 3 VMs each |
| netbsd11 / alpinebsd | 11.1 / 11.0 kernel at rel4 | FFSv2 / FFSv2ea | FFSv2ea, FFSv1, FFSv2 (+ tmpfs) | SF_LOG; alpinebsd uses NetBSD's tools |
| openbsd8 | 8.0 | FFS2 | FFS2 vnd | securelevel 1 |
| dragonfly6 | 6.4.2 | HAMMER2 | HAMMER2, UFS | all 32 flag bits |
| omnios58 | r151058 | ZFS | ZFS datasets, tmpfs | system attributes, SIDs, named attributes |
| haiku6 | hrev59866 | BFS | BFS | rustic only |

## (c) Redundancy

| Shared cell | Guests | What really differs |
|---|---|---|
| FreeBSD 15.1, ZFS root + UFS md, the same check.sh (8 lines differ: names and the extra.sh path) | freebsd15, freebsd15-geli, ghostbsd26-zfs | GELI sits below the pool, invisible to file metadata; GhostBSD's base packages and Install Station's dataset properties. freebsd14 runs the same check on the 14.5 kernel and its own OpenZFS, a real difference. All four had the same 19 needs in rel4 |
| FreeBSD UFS: the same check.sh and extras.sh | freebsd15-ufs, hardenedbsd15 (ghostbsd26) | The HBSD kernel (WITNESS on). ghostbsd26's extras.sh makes its md with `newfs -U -t`, no journal, plus sparse copies (13 lines). Same 21 needs |
| Kernel 7.0.0-43 + OpenZFS 2.4.1, fs_roundtrip zfs-ds and ext4 | ubuntu26-zfs, ubuntu26-zfsmirror | Encryption and mirroring sit below the datasets; zfsmirror has no file-pool run and no real.sh |
| Same check (10 lines differ, same 52 needs) | ubuntu24, ubuntu26-zfs | Kernel 6.8 vs 7.0, OpenZFS 2.2.2 vs 2.4.1. ubuntu24's ZFS stores every set atime as 0: a real difference |
| 6.12.115+deb13.1: ext4 /boot-like and ext4 pc | debian13, devuan6, debian13-stack, mx25, debian13-zfs, **and both hosts** (same kernel, OpenZFS 2.3.9-0+deb13u1, Debian 13 mkfs tools) | The root-like FS: btrfs, ext3, ext4 on LVM/LUKS/md, or ext2 |
| 7.0.0-42, ext4 + tmpfs (inner.sh: 15 lines differ) | mint22, zorin18 | f2fs and f2fs+ vs nilfs2. mint22 and mx25 also share a need set |
| 7.2.15, btrfs zstd:1 + SELinux | fedora44, fedora44-uefi | BIOS vs UEFI; ext4 /boot vs XFS /boot + vfat |
| el10, XFS + SELinux | rocky10, rocky10-lvm | LVM under XFS; a /boot partition inside the tree |
| 7.2.14, ext4 | arch, artix-s6, artix-dinit | arch1 vs artix1 builds; separate scripts |

Other kinds of repetition:
- **Inside each guest:** restore over immutable files, the strictatime backup pair, and the cross-tool directions. The user has called cross-tool compatibility a courtesy (memory `cross-tool-compat-courtesy.md`).
- **Across suites:** GuestTest1's backup column is a full drive.py round (gt1-rel2) or a reused SticTest1 round.
- **Across rounds:** in rel4 every guest's verdict set matched its previous passing run (REPORT.md).
- **Yield since 2026-11-04:** about 190 guest runs (st2a, rel1, rel2, gt1-rel2, rel3, rel4). Their FAIL lines were all harness or judge issues (omarchy's NOCOMPRESS, fixed in 2f5b4f8; haiku6's restore2 mtime, fixed in 4cdda9a; ghostbsd26's new check) or freebsd14's ABI (fixed in 22a5089). No archiver defect. The btrfs preallocation fix (rustic_core ed28fe0) cites a 13.4 GiB restore, a size no fixture here reaches.

**Caution:** the user's standing answer is that "every Linux guest gets its own pass" (memory `stictest1.md:48-49`).

## (d) Existing knobs

- **drive.py** (`drive.py:51-77`): a guest list, `--exclude`, `--first`, `--hosts`, `--slots` (default 3; rel4 used 2 because HoltTest1All ran too), `--hot-c`, `--poll`, `--bin`, `--out`, `--private-copy`, `--dry-run`, `--self-test`.
  - `--logs-mark` gives resume: a guest already started under a mark never starts again, and one that vanished is marked LOST.
  - `--history` sets each guest's estimate from its newest passing run, with README.md:125 as the fallback (`:194-215`).
- **What drive.py can't pass:** it gives each check only BIN, PORT, MARK-GUEST and STC_LOGS (`:730-735`). So there is no NEED_FILTER and no environment knob in a drive.py round.
- **NEED_FILTER** (check.sh's 4th argument): mostly it only filters the printed lines (`debian13/check.sh:54`). It skips whole VMs only in the three UFS checks (`freebsd15-ufs/check.sh:50-56`).
- **Environment knobs** (manual runs only):
  - `INVENTORY=1|only`
  - `FSLIST` (the mint family) and `FSS` (cachyos, omarchy, tumbleweed) to choose the scratch FS types
  - `MEMORY` and `RT_TIMEOUT`
- **affected.sh** maps changed paths to run-all.sh's 7 platforms, not to SticTest1 guests. Almost any restic or rustic_core change selects all of them, and a testvm/stictest1 change selects none (`affected.sh:179-215`).
- **run-all.sh** (a different runner) has `--platforms`, `--only`, `LINUX_FS` and `SPARSE_FS`.
- **expected-coverage.json** holds per-FS feature baselines for 10 file systems.
- **The wear log** (`/bkoscratch/wear/jobs.log`) will now record each `--reserve` job's disk writes.

## (e) Where time and writes go

**Rounds:**
- rel2: 32 guests in 37 min. gt1-rel2: 31 guests in 37 min.
- rel4 main pass: 36 guests in 65 min, 2 per host (1.94/1.96 VMs at once), Tctl at most 78.9 °C (Chopin) and 67.1 °C (Cinqcent).
- rel4's UFS three: 20 min. rel4's two new guests: 9 min.
- All of rel4: 16,899 guest-seconds (4.7 h).
- By family: ZFS-Linux (4 guests) 20%, FreeBSD-ZFS (4) 18%, the mint family 16%, the debian13 family 10%, Arch-likes 8%, the UFS three 7% (but 3 VMs each), Fedora-likes 5%, the other 12 guests 16%.
- The cap is room: at rel4's start Chopin's budget was 28.9 GiB of RAM disk and 19.2 GiB of memory (driver.log), so two 13 GiB ZFS guests fill a host's RAM disk.

**Inside guests** (measured from log file mtimes):

| Guest (s) | Breakdown |
|---|---|
| ubuntu24 (971) | 3 fs_roundtrip runs: 266 + 302 + 307 s = 90%. Boot and setup ≈35, real.sh 43, non-root 13 |
| freebsd15 (770) | roundtrip.sh 124/103 s same-tool, 230/219 s cross-tool (second backup plus node walks): 88%. extra.sh 50, non-root 14 |
| debian13 (479) | **rt_nodes walks 365 s (76%)**: two of them re-walk the whole tree just to keep the ancestor's line (`inner.sh:236`; `rt_path_nodes` at `rt-common.sh:292` reads one tree per level). 4 backups 30, 12 restores ≈50 |
| fedora44 (205) | rt_nodes 131 s (64%), backups ≈5, restores ≈25 |
| dragonfly6 (439) | 5 boots in turn; cross-tool directions 110 s vs 66-70 s |
| mint22 (781) | Boot 13 s, then 4 FS passes in 12.8 min (no finer timestamps) |

**Writes:**
- Almost everything goes to RAM: /mnt/bko-ram (QEMU snapshot files and spare disks), /dev/shm (the `--ram` overlays of dragonfly6 and the UFS three, which the ledger doesn't count), or guest tmpfs (the mint family).
- **SSD writes by code path:**
  - aerynos's overlay and its two raw disks.
  - ubuntu26-zfsmirror's spare file. Estimate: about 9 GiB of repositories plus the ext4 images per run; how much reaches the disk is unmeasured.
  - Logs: 44 KiB to 7.6 MiB per guest, about 59 MiB for all of rel4 (du on both hosts).

## Gaps in this evidence

- **No SSD bytes measured for any SticTest1 run.** The wear log started on 2026-11-07 around 04:02Z on Cinqcent and 04:49Z on Chopin, after the last SticTest1 run. The SSD figures for aerynos and ubuntu26-zfsmirror are estimates from the code.
- **Kernels:** aerynos's is not recorded. pop24's running kernel is ambiguous (header says 6.16; 7.2.15 is also installed). mint22's serial log shows two kernel strings.
- **LSMs** are not recorded for cachyos, omarchy, zorin18 and ubuntu26-zfsmirror. OpenZFS versions on the FreeBSD guests are not recorded.
- **Time breakdowns** are measured on 6 guests only. Counts marked ≈ are estimates from the code. I read alpinebsd, netbsd11, omnios58, cachyos, omarchy and void-musl at summary level only.
- **Not checked:**
  - whether the guests' mkfs and attr tool versions match the hosts'
  - swap use during rounds
  - which tool bugs the 2026-11-02/03 rounds found first-hand
  - whether drive.py's driver logs from rounds before rel4 still exist