# HoltTest1 as implemented (researcher: holt)

Evidence: repo at a69b711, and `/bkoscratch/holttest1d/{r3all,raid1-6,r2all,r32}` on both hosts. Parsed copies and my scripts are in `/private/tmp/claude-501/-Users-mchobbit-git-backotter/6e7b60cc-f873-4450-892c-1b053e7a5606/scratchpad/t2/holt/`. Times come from report headers and file mtimes (±5 s).

## (a) Pipeline and cost

ht1.sh runs the 30 Linux guests. It hands the 13 BSD, illumos and Haiku guests to ht1-bsd.sh, and redox and sculpt2610 to ht1-offline.sh (ht1.sh:43-49). The last full run, r3all (2026-11-06 21:53–23:23 UTC, 1→3 slots per host), passed 40/40. It took 186 guest-minutes, 114–924 s per guest. README:269 claims 15–25 min per guest.

| Step | What runs | Writes (kept?) | Median: Linux (max) / BSD |
|---|---|---|---|
| source | Overlay per disk; boot; boot-state and health baseline; `dump` (ht1.sh:197-224) | src-I.qcow2 (kept, 0.2–304 MB), src.json | 23 s (55) / 19 s |
| backup | Linux: vm-mount --ro on the host, rustic-bko, fsmeta (226-245). BSD: inside a rescuer copy grown by 8 GiB, plus mtree (ht1-bsd.sh:468-495) | Linux: share/repo (kept, 0.04–4.4 GB) and src-meta.json. BSD: the repo stays inside rescuer.qcow2 | 19 s (97) / 104 s. dragonfly6: 719 s, of which rustic-bko took 666 |
| rescuer | Overlay of rescuer.qcow2 (debian13 with OpenZFS 2.4) or rescuer-uefi.qcow2 (ubuntu26-uefi), plus a 9p share and a decoy disk (256-285) | Overlay deleted (365); decoy kept | 39 s / 27 s |
| refusals | nomap, root and decoy refusals, then partition without --commit; each disk's first and last MiB must be unchanged (291-327) | refuse-*.out holds the full plan | 5 s / 3 s |
| stages | All 7 stages, each `--only S --commit` then `--only status`; UEFI guests always get --no-nvram (290, 330-359) | dst-I.qcow2, the restored disks (kept) | restore 20 s (164), boot 35 s (184) / 7 s, 1 s |
| verify files | fsmeta and `judge.py fs` (376-393). BSD: the rescuer boots again for mtree (ht1-bsd.sh:634-668) | dst-meta.json (kept) | 23 s (146) / 43 s |
| verify boot | Restored disks boot alone on --snapshot: boot state, health, dump, judge.py (394-429) | dst.json | 16 s (51) / 27 s |

| r3all totals | Minutes |
|---|---|
| source | 18 |
| backup | 46 |
| rebuild | 77 (rescuer boots 24, restore 18, boot stage 26) |
| verify | 45 |

VM boots per guest: Linux 3; BSD 5 (Haiku 6); offline 5, plus two raw disk copies (ht1-offline.sh:88-110).

**What's kept.** On a pass, ht1.sh deletes only the rescuer overlay; ht1-bsd.sh deletes every qcow2 (ht1-bsd.sh:696). r3all still holds 93 GB on Chopin and 55 GB on Cinqcent:
- 103 GB of restored qcow2s, 39 GB of repos, and 4 GB of fsmeta files and logs.
- The largest guests: pop24 16.7 GB, omarchy 15.8 GB, mint22 14.3 GB.

A full run writes an estimated 150–180 GB. r32 was stopped when Chopin's /bkoscratch filled.

**Variants.** Each variant gets its own directory, so source and backup run again (ht1.sh:64-67). None ran in r3all.

| Variant | What it does | Guests |
|---|---|---|
| bigger | Targets 1 GiB larger, sfdisk same_size=false, `judge --grow` | debian13-stack, aerynos, mint22 |
| oneshot | One `--through close --commit` | debian13, mint22, arch, alpine324 (all MBR, none stacked) |
| degraded | Linux only (ht1-bsd.sh:38) | debian13-stack, ubuntu26-raid |

**Yield.**
- r2all found two udev races in close (1626714, 2e6c1b5).
- r3all found nothing.
- The new RAID guests' first runs found three load bugs (78018db, 3671e96).
- r32 had eight failures, all while Chopin's disk filled.

## (b) Feature matrix

Sources: the r3all dumps (the raid guests' from raid1, raid2 and raid6), their recorded plans, and their boot-state logs.

Firmware and table codes: B = BIOS, U = UEFI, +SB = Secure Boot via shim (on by default with vm-run --uefi, vm-run:18-19,101). M = MBR, G = GPT.

Boot path codes:
- **D**: grub-pc, update-grub, initramfs-tools, dpkg
- **A**: grub-mkconfig, mkinitcpio, pacman
- **F**: grub2-install, grub2-mkconfig, BLS, dracut, rpm
- **E**: grub-efi, update-grub
- **FE**: Fedora's UEFI branch, grub2-mkconfig only
- **W**: write-table puts the tables and boot areas back

| Guests | FW, table | Storage | Boot | init |
|---|---|---|---|---|
| debian12 / mx25 | B, M | ext4 / ext2 root, ext4 | D (mx25 reports ID=debian 13) | systemd / sysvinit* |
| debian13 | B, M | btrfs (1 subvolume), ext4 | D | systemd |
| mint22 / zorin18 | B, M | f2fs / nilfs2 root, ext4 | D | systemd |
| devuan6 | B, M | ext3, ext4 | D, but grub-mkconfig | OpenRC |
| debian13-zfs, ubuntu24 | B, M | ZFS rpool, ext4 /boot | D | systemd |
| arch, artix-s6 | B, M | ext4 | A | systemd, s6 |
| artix-dinit, cachyos | B, M | JFS root, ext4 | A | dinit, systemd |
| alpine324 | B, M; 16 ram disks skipped | XFS, ext4 | grub-mkconfig, mkinitfs, apk | OpenRC |
| void-musl | B, M | btrfs (no subvolumes), ext4 | grub-mkconfig, dracut, xbps | runit* |
| nixos2605 | B, M | bcachefs, ext4 | grub-install from the Nix store; no initramfs | systemd |
| fedora44 / rocky10 | B, M | btrfs (3 subvolumes) / XFS | F | systemd |
| tumbleweed | B, M | btrfs: 10 subvolumes, 1 read-only, qgroups | F, plus SELinux relabel | systemd |
| rocky10-lvm | B, G with a BIOS-boot partition, active protective MBR | LVM → XFS | F, core.img in a partition | systemd |
| rocky10-raid5 | B, M ×3 | md RAID5 with bitmap → LVM → XFS; 3-way RAID1 /boot | F ×3 | systemd |
| ubuntu26-zfsmirror | B, M ×2 | md RAID1 /boot; encrypted ZFS mirror; LUKS2 keystore zvol | update-grub, dracut, grub-install ×2 | systemd |
| debian13-stack | U+SB, G ×2 | md RAID1 /boot; md RAID1 → LUKS2 (crypttab) → LVM → ext4 /, XFS /srv; 2 ESPs | E, initramfs-tools | systemd |
| ubuntu26-uefi | U+SB, G | ext4, swap file | E, dracut | systemd |
| ubuntu26-zfs | U+SB, G | ZFS bpool and rpool | E, dracut | systemd |
| ubuntu26-raid | U+SB, G ×4 | md RAID10 → LVM → ext4; 4 ESPs | E, dracut | systemd |
| fedora44-uefi | U+SB, G | btrfs (zstd), XFS /boot | FE | systemd |
| fedora44-raid | U+SB, G ×2 | md 1.0 RAID1 ESP, md RAID1 /boot, btrfs RAID1 on 2 devices | FE | systemd |
| aerynos | U, G | f2fs, PARTUUID fstab | bootctl, prebuilt initramfs | systemd |
| pop24 | U, G | ext4 | bootctl, initramfs-tools (kernelstub through a hook) | systemd |
| omarchy | U, G | LUKS2 without crypttab → btrfs (8 subvolumes), swap file | Limine (nothing to install), mkinitcpio UKI | systemd |
| freebsd14, freebsd15, ghostbsd26-zfs | B, G (ghostbsd: glabels) | ZFS zroot (freebsd14: a boot-environment clone; ghostbsd: legacy zroot) | W | rc |
| freebsd15-mirror | B, G ×2 | ZFS mirror | W ×2 | rc |
| ghostbsd26, hardenedbsd15 | B, G (ghostbsd: glabels) | UFS2 | W | rc |
| netbsd11 | B, M + disklabel | FFS | W, installboot | rc |
| alpinebsd | U, G with wedges and an unmounted ESP | FFS, NAME= specs | W, bootx64.efi | s6* |
| openbsd8 | B, M + disklabel (DUID) | FFS | W, installboot -r | rcctl |
| dragonfly6 | B, M + disklabel64 | HAMMER2, UFS /boot | W, disklabel64 -B | rc |
| omnios58 / omnios58-mirror | B, G + ESP image (mirror: ×2) | rpool with a boot-environment clone, legacy mounts, swap zvol / mirror, legacy bootfs | W, bootadm, installboot | SMF |
| haiku6 | B, SATA, M | BFS | W, makebootable | –* |
| redox / sculpt2610 | B, q35, G with BIOS-boot and an ESP image | RedoxFS / GENODE ext2 | W from Linux, dump --disk | console / screen |

\* The boot-state check is empty on these guests (logs/source-bootstate.txt), so it proves nothing there.

Two guests have never been a HoltTest1 source:
- **freebsd15-ufs**, the FreeBSD rescuer. Neither host has a run directory for it, yet README:41 lists it as passing.
- **freebsd15-geli**. Its r2all run says "source boots (no SSH)", because no one types its passphrase.

**Duplicates.** These differ only in an osrel row, or in traits only the harness reads:
- {arch, artix-s6}
- {artix-dinit, cachyos}
- {debian12, mx25}: ext2 and ext4 go through the same probe/ext.rs and mke2fs
- {debian13-zfs, ubuntu24}
- {freebsd14, freebsd15, ghostbsd26-zfs}
- {ghostbsd26, hardenedbsd15}
- {fedora44, rocky10}
- Each mirror guest only adds a disk to its base guest.

**Paths only one guest exercises.** Each non-Linux family's guests are also the only ones exercising that family's code.

| Guest | Paths |
|---|---|
| debian13-stack | crypttab, LUKS on md, LVM on LUKS |
| fedora44-raid | md metadata 1.0, multi-device btrfs |
| rocky10-raid5 | RAID5, XFS sizes on an LV |
| ubuntu26-raid | RAID10 |
| rocky10-lvm | GPT on BIOS, active protective MBR |
| ubuntu26-zfsmirror | ZFS encryption, keystores() |
| ubuntu26-zfs | bpool |
| tumbleweed | read-only subvolume |
| omarchy | Limine |
| aerynos | systemd-boot, prebuilt initramfs |
| pop24 | bootctl with initramfs-tools |
| nixos2605 | bcachefs, Nix GRUB |
| alpine324 | mkinitfs, apk |
| void-musl | xbps |
| zorin18 | nilfs2 |
| devuan6 | ext3 |
| freebsd14 | boot-environment clone |
| omnios58-mirror | legacy bootfs |

**Paths no guest exercises:**
- LUKS1, crypttab key files, ZFS keyformat=passphrase (ht1.sh:313-315)
- raidz, log or special vdevs; md RAID0, RAID6 or 0.90; non-linear LVs
- XFS external logs; exFAT, NTFS, ISO 9660
- MBR logical partitions, a hybrid MBR, 4K sectors
- NVMe or SATA disks on Linux
- GRUB-EFI without shim; systemd-boot under Secure Boot
- efibootmgr entries: planned for 7 guests, never run; --removable
- GELI
- oneshot on a GPT or stacked layout; degraded ZFS mirrors

## (c) Source file → guests (basis for a holt affected.sh)

The code path is chosen at run time, from Family::current(), os.family and os.id. Only sys.rs:343 and load/mod.rs:30 are compile-time gated. So every change means rebuilding all seven binaries, but only these guests need testing:

| Code | Guests |
|---|---|
| json, bytes, sys, parttable reading, load/{mod,layout,plan,exec,status} | all |
| a row of osrel.rs SUPPORTED | that row's guests |
| report.rs, blockdev, mounts, boot.rs; target.rs Linux scan | 30 Linux (the scan also serves redox and sculpt2610) |
| efi.rs, esp_inventory, mkfs_fat | 9 UEFI Linux |
| package readers: dpkg · pacman · rpm · apk · xbps | 14 · 5 · 7 · 1 · 1 |
| probe/md, md(), mdsb.rs | debian13-stack, fedora44-raid, rocky10-raid5, ubuntu26-raid, ubuntu26-zfsmirror |
| probe/luks, luks() | debian13-stack, omarchy, ubuntu26-zfsmirror |
| probe/lvm, vg() | debian13-stack, rocky10-lvm, rocky10-raid5, ubuntu26-raid |
| probe/zfs, zfs_section, pool(), datasets() | 4 Linux, freebsd14, freebsd15, freebsd15-mirror, ghostbsd26-zfs, omnios58 ×2 |
| probe/btrfs, mkfs_btrfs, btrfs_inside | debian13, fedora44 ×3, omarchy, tumbleweed, void-musl |
| probe/xfs, mkfs_xfs | alpine324, debian13-stack, fedora44-raid, fedora44-uefi, rocky10 ×3 |
| probe/ext, mke2fs | 22 Linux, sculpt2610 |
| f2fs · nilfs2 · jfs · bcachefs | mint22, aerynos · zorin18 · artix-dinit, cachyos · nixos2605 |
| sfdisk_script | 30 Linux |
| writetable.rs, other/load.rs | 13 non-Linux, redox, sculpt2610 |
| boot(): grub-BIOS · Nix · grub-EFI · Fedora EFI · bootctl · Limine | 20 · 1 · 4 · 2 · 2 · 1 |
| initramfs(): initramfs-tools · dracut · mkinitcpio | 10 · 12 · 5 |
| other/freebsd · labelled · dfly · illumos · haiku · offline | 6 FreeBSD-family · netbsd11, alpinebsd, openbsd8 · dragonfly6 · omnios58 ×2 · haiku6 · redox, sculpt2610 |
| probe/ufs | ghostbsd26, hardenedbsd15, netbsd11, alpinebsd, openbsd8, dragonfly6 |
| redact.rs, report-bug | none |

A sharper selector is already half built:
- Every guest's full plan is on the hosts, in r3all/GUEST/logs/rebuild-refuse-decoy.out (40 plans).
- bugreport::check_plan (bugreport.rs:465-521) re-plans any src.json on a fake rescue tree with blank disks and stubbed tools.

Diffing old versus new bko-holt plans across the 45 recorded dumps (11.4 MB) would name exactly the guests whose load changes.

## (d) Without VMs: what is tested today, and what could be

**Today**, grep finds 243 #[test]s. I didn't run them.
- **Parsers** are tested on synthetic bytes. realtools.rs (13 tests) checks images made by mkfs against blkid, dumpe2fs, xfs_db and sfdisk. It needs no root, but runs on Debian 13 only.
- **scenarios.rs** runs 9 fake Debian 13 machines through the whole dump, and plans each one again. `--sysroot` lets dump and load read such trees without running commands (main.rs:44-47, load/mod.rs:105-106), and refuses --commit (exec.rs:73-76).
- **Load** has tests for refusals (target.rs:490), plan and status on fake trees, one sample layout's commands plus variants (14 tests), and write-table round trips (writetable.rs:258-286).
- **No VM-free tests:**
  - other/load.rs (525 lines), other/offline.rs and load/exec.rs, which have no tests at all.
  - The non-Linux report()s, beyond their parsers.
  - All the writing stages.

root_stack.rs has two #[ignore] tests, and they check dump only. layload.md:536-540 plans a second test layer (load onto loop devices, dump again, compare), but it isn't built.

**Possible:**
1. **Golden plans.** A plan depends only on the dump, the target scan and which tools are present (plan.rs:61-78). Checking every guest's plan takes milliseconds each, with no VM.
2. **Replayed dumps.** Every read goes through Sys (raw devices via ByteSource::read_at), and every command through Runner (sys.rs:1-4, 213-242). The Linux dump runs only rpm, systemd-detect-virt, zpool, zfs and btrfs; the other systems use about 20 tools. One recording per image generation would let us check dump(recording) == src.json for every guest, with no VM.
3. **Image files, no root.** writetable::plan() is pure (writetable.rs:67), and sfdisk and every mkfs can write to regular files. Running each guest's partition and format commands on sparse images, then probing the result, would have caught rocky10-raid5's XFS geometry bug: raid1's judge.txt shows DIFF on ag_blocks, blocks and size_bytes. offline.rs already reads image files (offline.rs:26), but refuses all but Redox and Sculpt (offline.rs:77).
4. **Loop devices.** md, LUKS, LVM and ZFS need root and names that are unique on the host (README:194-196). That means one throwaway rescuer VM, not the shared hosts. ht1-debug's RAM loop rebuilds a guest in 38–91 s (README:265-268).
5. **Dry runs** exist. But status checks only extents, IDs, nodes, UUIDs and mounts (status.rs:55-183); geometry is judged only after a boot.

**Levers:**
- Have ht1.sh delete on a pass, as ht1-bsd.sh does.
- Cache each guest's dump and repo per image generation, then run only `rebuild verify` (README:177-178).
- Let variants reuse their base run.
- Use HT_RAM=1 (ht1.sh:27-28).
- Run the refusals once per rescuer family.
- Narrow fsmeta for holt-only changes: r3all holds 2.6 GB of its JSON.

## Gaps in this evidence
- I ran no tests, builds or VMs, and counted the tests with grep.
- Step times come from file mtimes. I couldn't separate the offline guests' steps.
- Write volume is an estimate from du, not a measurement.
- The matrix comes from the r3all and raid dumps. alpinebsd, ubuntu26-zfs and nixos2605 changed later (run r4u).
- omnios58-mirror's raid6 report says FAIL (1), on mtree lines the committed EXTRA_BOOT_RE now allows; the README says it passes. fedora44-raid's raid2 run fails only on a judge rule added later (72858ed). No rerun report exists for either.
- I read commands.rs and report.rs only at their dispatch points.