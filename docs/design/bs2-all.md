# BackShot2All: all of BackShot2, every OS family, fresh (plan, agreed)

2026-11-14. Plan agreed with the user in conversation.
Revised the same day: parallel lanes, and the word runs early.
**Status (2026-11-25):** A0, A1 and the Linux lane (A2, A7, A9) built; A3-A6
and A8 not.  `bs2 BackShot2All` runs 54 Linux sources and lists 40 sources
and tier 3 as SKIPPED: PARTIAL PASS at best ("The Linux lane, built" below).
Since 2026-11-25 a space preflight, leaner tools and slimmed big sources
("Room, slimming and placement" below): vanilla3 is off the SSD lane, and
`plan` says ~7.8 h on six hosts.  The first full run (bs2all-20261125-120413)
was paused and then **aborted by the user** the same day ("The first run,
aborted" below); the next run starts fresh from current main, when the user
says so.
It fills in the word that `docs/design/backshot2.md` ("Words") reserved:
"`BackShot2All` | reserved for all of BackShot2, fresh, later". est. marks an
estimate. `backshot2.md` is `docs/design/backshot2.md`; its words (case,
tier, the three rules, INVALID, the corpus) are used as defined there.

## Summary

- **BackShot2All** runs BackShot2's generator, fresh, over every source of
  every OS family bko-holt supports: Linux, the BSDs, illumos, Haiku, Redox
  and Sculpt OS. It includes the ZFS, md and multi-disk guests, BackShot2Alpine's
  full run, the corpus, the leftovers set and 5 Thinkpad cases.
- **It is a superset of BackShot2Alpine** (the user's choice): one tool
  (`testvm/backshot2/bs2`), one generator, case format and corpus. Content
  drift and the option sweep, which are Alpine-only in milestone 1, extend to
  every source.
- **Shape stays the same** in this version. Shape changes (`/home` split,
  adding or removing LUKS or LVM, UEFI and BIOS, GPT and MBR) are BackShot2
  phase 2. They are specced after BackShot2All passes once, and join All when
  built.
- **The word runs early** (the user, 2026-11-14, replacing "waits for all
  of it"): `BackShot2All` runs every family that is built and lists each
  unbuilt one as SKIPPED (unbuilt: A-piece named) in its plan and report. A
  run with any SKIPPED family is a partial All: it reports PARTIAL PASS, not
  PASS, and doesn't count as the full All in CarpetBeater's record.
- **Size:** about 64 sources today. The QEMU tiers have ~1,250 cases, and a
  run is ~15-16 h on the four hosts (est.).
- **Build:** 16 days of effort (est.), built in parallel lanes: about a week
  of calendar time (est.) to all families, with the first (Linux) run after
  about 4-5 days (est.).

## Naming

The naming rules are the same as for the QEMU tests: the number is the
generation, a suffix narrows the scope, and All means the whole generation,
fresh. So:
- `BackShot2Alpine` is BackShot2 narrowed to Alpine;
- `BackShot2 [SOURCE...] [N]` covers named sources, drawing what the ledger
  says is due;
- `BackShot2All` covers every source and every set, fresh: ledger rows don't
  excuse a case. This matches HoltTest2All, SweepTest2 and HoltTest1All.

"All" means all of BackShot2 *as specced*. When phase 2 (shape changes)
lands, All grows to include it, the way HoltTest1All grew with the guest list.

## Sources

Sources are every guest in HoltTest2's corpus list
(`holt/tests/corpus/index.tsv`) **at run time**, so the distros being added
(AnduinOS, elementary, KDE neon, BigLinux, Garuda, PikaOS) join without an
edit here. It also takes BackShot1 library runs (`run:...`) for the Thinkpad
tier. Counts as of today, est.:

| Family | Sources | Count |
|---|---|---|
| Alpine | `alpine324`, `-uefi`, `-lvm`, `-luks` (BackShot2Alpine's) | 4 |
| Other Linux, one disk | aerynos, arch, artix-dinit, artix-s6, biglinux, cachyos, chimera, chimera-uefi, debian12, debian13, devuan6, endeavouros, endeavouros-grub, fedora44, fedora44-uefi, gentoo, gentoo-systemd, kinoite44, manjaro, mint22, mx25, nixos2605, omarchy, pop24, rocky10, rocky10-lvm, tumbleweed, ubuntu24, ubuntu26-luks, ubuntu26-uefi, vanilla3, void-musl, zorin18 | 33 |
| Linux ZFS, md, multi-disk | cachyos-zfs, chimera-zfs, debian13-zfs, debian13-stack, fedora44-raid, proxmox9, proxmox9-mirror, rocky10-raid5, ubuntu26-raid, ubuntu26-zfs, ubuntu26-zfsmirror | 11 |
| BSD | freebsd14, freebsd15, freebsd15-mirror, ghostbsd26, ghostbsd26-zfs, hardenedbsd15, hardenedbsd15-zfs, dragonfly6, netbsd11, openbsd8, alpinebsd | 11 |
| illumos | omnios58, omnios58-mirror | 2 |
| Haiku, Redox, Sculpt OS | haiku6, redox, sculpt2610 | 3 |
| | | **64** |

**Proxmox VE 9** (2026-11-15, docs/design/proxmox.md): `proxmox9` (ZFS raid0)
and `proxmox9-mirror` (raid1, an ESP on each disk) join the ZFS row. (The
other guests added since 2026-11-14, PikaOS, Kali, KDE neon and the rest,
join at run time as Sources says, and aren't counted here.) Their own rule
is built: the `proxmox-boot` trait (`bs2lib/families/proxmox_boot.py`:
proxmox-boot-tool's unmounted ESPs, which load rebuilds from the volume IDs
it lists), with their golden in `tests/golden/linux.json`; the unit tests'
smoke draws 300 cases of each, all valid under the rules (tier 0's dry runs
not run yet). Their tier 1 and 2 (source map, `SECS`) are A2's, their drift
on the mirror's second disk A3's: about 15 min more agent time for each
lane (est.). A run gains 2,000 tier 0 cases (~10 min), 40 tier 1 and 10
tier 2 cases (both are UEFI): ~4 slot-hours at ~5 min a case, about 0.5 h
of wall clock over 8 slots (est.). Room per case as HoltTest1's
(`holttest1d/ram-sizes.tsv`): D 9 and 13 GiB, MEM 4, so
`proxmox9-mirror`'s cases stay on Chopin and Cinqcent (a small host's
10 GiB RAM disk doesn't hold D 13), as `ubuntu26-zfsmirror`'s (D 15) do.

**Solus 4.9** (2026-11-15, docs/design/solus.md): `solus49` is one of those
run-time joiners (its corpus dump is in `index.tsv`), with no module of its
own. The registry makes it `linux` with the `systemd-boot` trait:
clr-boot-manager's loader entries are BLS ones, and its initrds are
prebuilt, so no generator trait (`tests/test_families.py` names it; the
smoke draws 300 valid cases). It is the one source whose ESP is `/boot` and
out of fstab. Nothing here needs to know: tiers 1 and 2 run inside ht1.sh,
whose `SOLUS_BOOT` runs `clr-boot-manager mount-boot` before each dump, and
judge.py's `SOLUS_KEPT` holds the whole ESP to the source's bytes. It isn't
in `tests/golden/linux.json` yet, as Kali and the other late joiners aren't.

**Guix System 1.5.0** (2026-11-24, docs/design/guix.md): `guix15` (UEFI,
ext4) and `guix15-bios` (BIOS, Btrfs `@`, `@home`, `@gnu`) join the
one-disk row with a family of their own, `bs2lib/families/guix.py` (golden
`tests/golden/guix.json`; they leave `linux.json`): no renumbering of the
swap (config.scm's swap-devices isn't in the dump) or, with two or more
generations, of a partition mounted outside / and the store (older
generations' fstab isn't either); rule 3 over every generation's boot
parameters; content recipe `guix-small-1`; tier 0 with dry runs in the
system profile's tools. `bs2` runs them (`GUIX_SOURCES`, tier 2 for
`guix15` only), each case at HoltTest1's room (D 7/6, MEM 4: a host whose
`cmp-run.sh --ceiling` holds it, so not choubox's 3.0 6.0; `held()` leaves
such a host out of a source's holders). First run bs2a-20261124-1028: tier 0 229 + 220 PASS, tier 1 and 2
guix15 10 + 3 PASS, guix15-bios 7 PASS and 3 harness FAILs (fail-007,
fail-008); fail-006 (bko-holt: renumbering / left grubx64.efi's prefix
naming the old partition) was written by hand from guix15's image; all
three fixed (e8cbfd4 for bko-holt). Second run bs2a-20261124-1111 with the
fix: tier 0 229 + 220, tier 1 20 + 20, tier 2 5, all PASS (~235 s a guix15
case, ~153 s a guix15-bios one, contended). On
BIOS the same prefix is in core.img (LZMA-compressed, written back by
grub-bios-setup), which load can't renumber: the generic rule 3 already
draws no renumbering of guix15-bios's / (grub.cfg searches by label), and
that stays a bko-holt limit, not drawn.

**Vanilla OS 3** (2026-11-15, docs/design/vanillaos.md): `vanilla3` (ABRoot
v2: two btrfs roots on thin LVs of `vos-root`, the init LV, an ext4 /boot
and a btrfs /var on `vos-var`, one disk, UEFI with Secure Boot) joins the
one-disk row. Its rules are built: the `abroot` trait
(`bs2lib/families/abroot.py`: the master grub.cfg's entries, each root's
abroot.cfg, ABRoot's kargs as rule 3 texts; load restores both roots, the
init LV and /boot as they were) and the generic `lvm-thin` trait
(`lvm_thin.py`: no `lv_split` or `lv_resize` of a thin pool's data,
metadata or spare LV, which load's lvcreate sizes and places itself), with
its golden in `tests/golden/linux.json`; the smoke draws 300 cases, all
valid (tier 0's dry runs not run yet; Vanilla's root has no sfdisk, so the
table's dry run takes the host's). Its tier 1 and 2 are A2's, and they
can't run in RAM: HoltTest1's room for it is D 30 GiB
(`holttest1d/ram-sizes.tsv`), over every RAM disk, so its cases need a
`/bkoscratch` mode in `bs2-host` (as ht1-queue.sh's `DISK_OVER=1`) on Chopin
and Cinqcent only. A run gains 1,000 tier 0 cases (~5 min), 20 tier 1 and 5
tier 2 cases at ~20 min each (two restored boots, one per root, as
HoltTest1's): ~8 slot-hours, ~4 h of wall clock on one disk slot per big
host (est.), so it should start first.

**Known findings are steered around, not waited for** (backshot2.md's
`generate.KNOWN` rule). OpenBSD's rustic-bko restore OOM in the 3 GiB image is
blocked until rustic_core bounds restore memory. Its tier 1 cases therefore
stay a corpus case, not a reason the word refuses. Haiku runs rustic-bko only,
as everywhere.

## What it runs

| Set | Per source (non-Alpine) | Alpine | Tier |
|---|---|---|---|
| tier 0 | 1,000 cases | 1,000 | plan and dry runs |
| tier 1 | 20 cases | 200 | QEMU |
| tier 2 | 5 cases, UEFI sources only | BackShot2Alpine's share | QEMU, NVRAM kept |
| corpus | every case in `cases/`, tier 1 | the same | 1 |
| leftovers | the set (L1-L9), strict | | 1 |
| tier 3 | 5 Thinkpad cases in all, from BackShot1 library runs, rotating distros | | the Thinkpad |

- **Content drift and the option sweep** go to every source. The content
  recipe is per filesystem, with only what that filesystem holds: ext4, XFS,
  btrfs, ZFS, UFS/FFS, HAMMER2, BFS, RedoxFS, FAT. `--no-nvram` and
  `--removable` are drawn only where the firmware is UEFI.
- **Tier 3** stays at 5 cases per round (the user, 2026-11-12). Their sources
  are BackShot1 library runs, a different distro each round in rotation. LUKS
  cases unlock from the key file on mybig512 (`bs1-luks-unattended`), so
  they're hands-free. Anything still needing a typed passphrase or the console
  goes first in the Thinkpad block (front-load rule).

**Wall clock (est.):**
- ~1,160 non-Alpine tier 1 cases at ~4-5 min each (BSD and illumos slower)
  come to about 87 slot-hours. Over ~8 RAM-limited slots across the four
  hosts, that's ~11 h.
- Tier 2 adds ~1.5 h, BackShot2Alpine's part ~2 h, and the corpus and the
  leftovers set ~1 h.
- **About 15-16 h**, an overnight-plus run. The Thinkpad's 5 cases add
  ~1-2 h at the end.
- `bs2 BackShot2All plan` prints the real figure from the hosts' slots and
  each source's measured seconds (`SECS` per source, from B12).

## Fit

- **Ledger:** Test2's ledger, family `bs2`, keyed as backshot2.md says (case
  hash, bko-holt and rustic-bko sha256, source generation). All writes rows
  like any run, but ignores them when choosing cases.
- **Placement:** bs2's existing `split()` sends each case to a host that holds
  its source, by slots (hosts.json `bs2_slots`). Cage and Copland take only the
  guests their caches hold. Every run stays in RAM under `cmp-run.sh
  --reserve`, WEAR_TAG `bs2`, and keeps within HoltTest2's per-source wear
  budgets for the big guests.
- **Non-Linux tier 0:** a Linux host can't chroot into a BSD, illumos or Haiku
  root to run its own `newfs`, `gpart` or `zpool` dry runs. For those sources,
  tier 0's dry runs run in one booted source guest per source and host, batched,
  so the 1,000 cases cost one boot, not 1,000.
- **CarpetBeater:** BackShot2Alpine every round, as now. **BackShot2All takes
  its place every second round** (about monthly), and once after any change to
  load's command code (`holt/src/load/commands.rs`). Until the word is built,
  CarpetBeater runs BackShot2Alpine every round.

## What must be built first (est.)

| # | Piece | Effort |
|---|---|---|
| A0 | BackShot2Alpine run to phase 1's end (backshot2.md), measured with the reuse switches on and off | 0.5 d |
| A1 | B11, grown: rule 2 tables and source quirks for the other Linux sources: dracut, mkinitcpio, booster, systemd-boot, shim, Limine, snapper, ostree (kinoite44), ABRoot and LVM thin pools (vanilla3: `abroot`, `lvm-thin`, built 2026-11-15), NixOS generations, Gentoo, Chimera. Plus rule 3's loader paths (Limine, systemd-boot entries, NixOS's generated GRUB) | 3 d |
| A2 | B12: tier 1 and 2 for those sources: source map, `SECS` per source, which cases go to tier 2; a `/bkoscratch` mode for sources over the RAM disk (vanilla3, D 30 GiB) | 1 d |
| A3 | ZFS, md and multi-disk drift, shared by Linux, BSD and illumos: ashift, vdev partitions placed and sized, md metadata version and offsets, per-disk targets, member placement. Rule 3 for pool and array names | 2 d |
| A4 | BSD: rule 2 tables (gpart, newfs, bsdlabel and disklabel for OpenBSD and NetBSD, HAMMER2), tier 0 dry runs in a source guest, rule 3 (`/dev/ada0p2` in fstab, `vfs.root.mountfrom`, boot code) | 4 d |
| A5 | illumos: EFI and VTOC labels, ZFS root, the loader and bootadm, rule 3 | 1.5 d |
| A6 | Haiku, Redox, Sculpt OS: partition drift, BFS block size, their rule 3 | 1.5 d |
| A7 | Content recipes per filesystem | 1 d |
| A8 | B10: tier 3 glue, a case onto the Thinkpad through backshot1. Copy the library runs' `layout.json` and repositories from mybig512 to a host | 0.75 d |
| A9 | The `BackShot2All` word, its `plan`, SKIPPED families (naming the unbuilt piece) and PARTIAL PASS, its report | 0.5 d |
| | **≈ 16 d** of effort, then a first run and its fix cycle | |

**Order: parallel lanes** (the user, 2026-11-14).
1. **Serial start:** A0, then A1. A1 also fixes the **family interface**: one
   module per family under `testvm/backshot2/bs2lib/` (its rule 2 tables,
   quirks, rule 3 paths, content recipe hooks), registered in one table, so
   later lanes add files rather than edit the same generator code.
2. **Parallel lanes, one session each, once A1 lands:** A2+A7 (Linux tiers and
   content recipes), A3 (ZFS, md, multi-disk), A4 (BSD), A5 (illumos), A6
   (Haiku, Redox, Sculpt OS), A8 (Thinkpad tier). Each lane owns its family
   module and its tests; shared files (the registry, `generate.KNOWN`, the
   README) are edited by one lane at a time, in small commits on main.
3. **A9 early:** the word with SKIPPED families lands with the Linux lane, so
   the first partial `BackShot2All` runs as soon as A2+A7 are in. Each later
   lane's family joins the next run.

Each family can also run under `BackShot2 SOURCE...` as soon as it's built.
**Re-estimate after A0 and A1:** their measured time sets the rate for the
rest (recent specs of this size ran well under their estimates).

### A0 and A1, measured (2026-11-14)

**A1** (be13648) took about 1 h of agent time against its 3 d estimate
(53 min wall clock, ~25 of it queued for a host). Its tier 0 smoke: 100 of
100 PASS on each of 45 Linux corpus guests.

**A0** took about 11 h of wall clock against its 0.5 d. Most of that went on
two harness bugs the first real run found, plus host contention:
- **False PASSes (022f00e).** A resumed rescuer read its targets' sizes from
  images QEMU had locked, so ht1.sh's phase loop died partway and still
  printed `RESULT: PASS`. The first run (bs2a-20261114-0655) was void. A PASS
  now needs every phase in both the report and `phases.tsv`.
- **Tier 2 never booted the restored disk (d5e8686).** OVMF matched its boot
  option by device path and rank, which moved between the two boots.
  `vm-run --disk-ports` puts each disk behind its own root port in both.
- **Generations moved mid-run.** Chopin's Alpine guests were updated at
  06:06, and Copland, Cage and Chopin moved to the vm-store's images at 12:00.
  A run must keep to hosts at one generation (`--hosts`). The tier 2 cases
  were therefore drawn again at the new generation, on Cage and Copland.
- **Contention.** Other sessions' reservations held Cinqcent to 2 of its 5
  slots most of the day, and it was off 10:00-11:20 for an NVMe swap.

Phase 1 results (run bs2a-20261114-0827, tier 2 in bs2a-20261114-t2n):

| | Result |
|---|---|
| tier 0 | 4,000 of 4,000 PASS (1,000 a source), with dry runs, ~0.28 s a case |
| tier 1 | alpine324 200/200, -lvm 200/200, -uefi 198/200 + 1 vm-mount flake (PASS on replay) + 1 HARNESS (another session's QEMU held a port), -luks 190/200 at 18:00 (the last 10 still queued on contended Cinqcent, none failed) |
| tier 2 | 30 of 30 PASS (10 each, uefi, lvm, luks) |
| tier 3 | not run: needs B10 (A8) |
| corpus | the five open findings (fail-001 to fail-005) still fail at their own checks, at tiers 0 and 1 |

No FAIL was a bko-holt finding, so nothing new was shrunk into `cases/`.

Seconds per tier 1 case (mean `wall_s` of PASSes):

| Source | Free host (Cage, Copland), reuse on | Reuse off | Cinqcent, contended |
|---|---|---|---|
| alpine324 | 40.3 | 73.5 (25 cases) | |
| alpine324-uefi | 56.4 | | 85.7 |
| alpine324-lvm | 59.8 | | 113.5 |
| alpine324-luks | 84.0 | | 140.5 |

- **Reuse on** saves about 45% (alpine324: 0.2/4.6/22.9/11.6 s for
  source/backup/rebuild/verify, against 12.5/5.1/40.1/15.0 off), with
  identical verdicts on the same 25 cases.
- **Tier 2:** 52-71 s for uefi and lvm, 74-98 s for luks.
- **Contention:** a contended big host runs a case 1.5-1.7 times slower than
  a free small one. Plan by the contended figure.

**Re-estimate.** Code ran at about a twentieth of its estimate; first real
runs ran at about twice theirs, because they find harness bugs and wait for
hosts. Each lane is therefore about 2-4 h of agent work plus one first real
run and its fix cycle, which is half a day to a day of wall clock.

| Lane | Was | Now (agent / wall clock) |
|---|---|---|
| A2+A7 (+A9) | 2.5 d | ~4 h / 1 d: the most sources, first tier 1 for 45 guests |
| A3 | 2 d | ~3 h / 1 d: big multi-disk guests, wear budgets |
| A4 | 4 d | ~4 h / 1-1.5 d: tier 0 in a booted guest is new code |
| A5 | 1.5 d | ~2 h / 0.5 d, after A3's zfs trait |
| A6 | 1.5 d | ~2 h / 0.5 d |
| A8 | 0.75 d | ~2 h / 0.5 d, plus the user's go-ahead to wipe the Thinkpad |

All six in parallel: about 1.5 days of wall clock to the first full
`BackShot2All`, bounded by host time, not code. The full run's own length
(about 15-16 h above) stands, at the contended rate.

### The Linux lane, built (2026-11-24)

A2, A7 and A9 in about 3 h of agent time against their ~4 h / 1 d (review
and one smoke round included).  testvm/backshot2/README.md "BackShot2All"
says how it works; in short:

- **A2** (9ec34de, e3e00a1, ec57929, 045bc14): `bs2lib/sources.py` reads the
  corpus at run time and classes each amd64 guest built (54: every one-disk
  Linux source with a family) or SKIPPED by piece: A3 15 (ZFS, md, more
  than one disk, from the dumps; includes ubuntu24, truenas25, void-zbm and
  -enc), A4 14, A5 4, A6 3, A8 2 (the realworld dumps), later 2
  (guix15-luks, LUKS1; uc24-fde).  UEFI from the dump; seconds a case
  measured for Alpine and Guix, else 0.7 of HoltTest1's (est.).  vanilla3,
  over every RAM ceiling, runs on `/bkoscratch` (`--reserve-ssd`, `HT_RAM=0`)
  on the `holt_disk_mode` hosts only, one SSD case at a time per host.
- **A7** (e50924c, 81d8fd5, f546085): GENERATOR 6, a content recipe per
  filesystem holding `/srv/bs2`: ext2/3/4, XFS, btrfs, f2fs, JFS, NILFS2,
  bcachefs (and 32-bit-time forms); ZFS with A3; FAT holds none; none for
  ostree (kinoite44) and Ubuntu Core.  Alpine's and Guix's draws unchanged.
  ABRoot's content lands in /var/srv (the `srv` hook); planting walks by fd
  and never follows a link out of the source.
- **A9** (cf78660, cfa8117, 2340354, 9539db4..d136502): `bs2 BackShot2All
  [plan]`, then `corpus RUN`, `leftovers RUN` (not chained: both take port
  2949) and `report RUN [--json]`: FAIL, INCOMPLETE, PARTIAL PASS or PASS;
  ledger rows say word BackShot2All.  No whole-run ledger row.
- **Smoke** (bs2a-20261124-1738, five hosts, nine sources over six
  recipes, 2 tier 1 and 1 tier 2 each): tier 0 653 of 653 PASS; tier 1 18
  of 18 PASS (content planted on ext-1, xfs-1, btrfs-1, f2fs-1, jfs-1,
  bcachefs-1); tier 2 fedora44-uefi and aerynos FAILed on by-path links
  only, a harness bug (tier 2's disk sits behind its own PCIe root port),
  fixed in judge.py `--disk-ports` (d1c0974), both PASS on replay.
  bs2a-20261124-1754: vanilla3 on chopin's SSD, tier 1 PASS in 810 s.  No
  bko-holt finding.
- **Time.** Measured tier 1 cases took 1.06 times sources.py's estimates
  (vanilla3 0.71); tier 2 1.0-1.8 times its tier 1.  `bs2 BackShot2All plan`
  says ~8.0 h (the queues ~7.3 h after the leads' ~135 min tier 0 sweeps,
  then the corpus and leftovers ~45 min) on six free hosts; with the
  measured ratios **~9 h on a quiet farm, ~12-13 h contended** (A0's 1.5
  times), est.  Each further lane adds its sources (A3's big guests the
  most).  Its SSD share: vanilla3's 25 cases, ~0.7 TB written on chopin and
  cinqcent a run (est.).

### Room, slimming and placement (2026-11-25)

The first BackShot2All start (bs2all-20261125-065134) filled cinqcent's
/bkoscratch before a case ran: nothing sized the sources' preparations and
tools.  testvm/backshot2/README.md "Sources and tools on /bkoscratch" and
"Slimming" say how each piece works; in short:

- **Space preflight and lean tools** (ddb8011, dd3ee16, 4ea50fb, 8ca9e01): a
  source's older preparations go once nothing uses them (`prune_preps()`;
  one a live case still uses stays, and `bs2-host room` reports it, so the
  preflight counts it as kept); tools (the root copy tier 0's dry runs
  chroot into) only on a source's lead and only for a family with dry runs,
  a leaner copy (kinoite44's from its deployment); and `bs2lib/space.py`
  works out each host's peak on /bkoscratch from `bs2-host room` and
  `space-sizes.tsv`, printed by `plan`, and a run refuses to start on a host
  that lacks it (30 GiB margin).
- **Slimming** (3be0b8f, 4780572, 8759aaf): a source whose full room is over
  the small hosts' `cmp-run.sh --ceiling` is slimmed in its preparation
  (`bs2lib/slim.py`: caches, logs, flatpaks, snaps, locales, docs, themes,
  fonts, firmware; never the layout, the boot chain, `/etc` or the content
  recipes' paths; btrfs snapshots left alone), 20 sources today.
  **ubuntu26-luks, kinoite44 and garuda stay full fat, the user's choice**
  (`slim.FULL_FAT`): three whole desktop guests (LUKS, ostree, snapper) at
  every tier.  A slimmed source's generation carries the recipe (`+slim1`),
  so its ledger rows name the slimmed source.
- **vanilla3 leaves the SSD lane**: slimmed, its room is D 12 (was 30, over
  every RAM ceiling), so its cases run in chopin's and cinqcent's RAM disks
  like the rest; no source needs `--reserve-ssd` now (~0 GB written to the
  SSDs a run, records only, against ~0.7 TB before).
- **Placement** (`BS2_WT=bs2all-wt bs2 BackShot2All plan`, 2026-11-25): the
  small hosts' ceilings (9 GiB RAM disk, 10.1 memory) still leave out 15
  sources each (the slimmed desktops whose room is still over them, and the
  three full-fat ones), choubox's (3, 6) all but five.  chopin and cinqcent
  take ~445 cases each on 5 slots, copland, cage and czerny ~270 each on 2,
  choubox 285 on 1 (alpine324's 200, artix-dinit, artix-s6, debian13, uc24);
  each host ~7.1 h (chopin and cinqcent after their ~135 min tier 0 sweeps),
  then the corpus (8 cases, ~16 min) and the leftovers set (9 steps on
  chopin, ~27 min): **~7.8 h est.**  The space preflight is OK on every host
  (peaks chopin +37 GiB of 306 free, cinqcent +41 of 145; the small hosts
  free room as their tools go).
- **The report counts cases, each once** (2026-11-25 review): seeds often
  draw the same case, so a tier plans its distinct cases (the leads' pick
  lists and sweeps' plans) and counts only their rows; `enqueue` queues a
  case once; `--sweep` and `--tier2` are refused for BackShot2All (decision
  5's sizes are the word's; `BackShot2` takes them).

- **The prepared repository is read from the SSD** (7007cfe, a0c40dd; user's
  decision): a shared-backup case (no content drift, one snapshot, about one
  case in four) binds its preparation's `backup/repo` read-only into its share
  instead of copying it into the RAM disk, and reserves `d_shared`
  (`slim-sizes.tsv`), 2-3.5 GiB less; reads only, so no SSD wear.  A case
  that makes its own backup still writes its repository in RAM.  If the bind
  fails, the case copies the repository only when the copy provably fits its
  reservation, else refuses as HARNESS (04384ac).
- **Per-case placement** (d5c70d0): a shared-backup case may go to a host that
  holds its `d_shared` (anduinos2, biglinux, endeavouros(-grub), mint22, mx25,
  neon24, solus49 and zorin18 on copland, cage and czerny).  `plan` uses it
  only where the run finishes sooner; for BackShot2All it doesn't (7.24 h
  against 7.16 h: the hosts are already balanced and the small hosts would
  spend ~36 min preparing).  Smaller runs gain (mint22+mx25+neon24 x20: 0.37 h
  against 0.42 h).
- Rejected on the way (2026-11-25): fresh installs on 6-10 GB disks and
  shrinking partitions.  Neither cuts the RAM peak, which is written bytes
  (the restored disk and the repository), not disk size.

### The first run, aborted (2026-11-25)

bs2all-20261125-120413 started 12:04 with bko-holt and tools from fdd6d27.
It was paused ~15:30 (stop files on every host) and aborted by the user;
each host's run folder has `stop` and `ABORTED`.  Rows when it stopped:
tier 0 30,162 PASS, 2,173 FAIL, 409 INVALID; tier 1 385 PASS; tier 2 16
PASS; no tier 1 or 2 failure.  The tier 0 non-PASS rows are two harness
bugs fixed during the run: Slackware's rescue disk names and XFS geometry
(e4d008a, 8a7ee5f, 35e08b4; 1,708 rows) and uc24's snap-based tools and
boot.snapcore records (b199bdc, a4da957, a98cd58, 3cd3dab; 409 INVALID,
465 FAIL), plus the XFS log limit (9017804, GENERATOR 8).  Not reached:
the rest of the queues, the corpus, leftovers and the report.

The next run needs the bs2all-wt tree and planner rebuilt from current main
and bko-holt rebuilt (a98cd58's message); every source re-prepares once and
each host rebuilds its saved rescuer once (7007cfe's `multidevs=remap`).

## The family interface (A1)

Fixed by A1, 2026-11-14. Each later lane is briefed from this section: a
lane adds a family by adding files under `testvm/backshot2/bs2lib/families/`
and its tests, never by editing the generator, the rules or the tables.

**Two kinds of module**, both in `bs2lib/families/`, one file each:
- a **family** (`Family`): exactly one per source. It is the OS and distro:
  Alpine, NixOS, Gentoo, Chimera, generic Linux (the rest of Linux), later
  FreeBSD, OpenBSD, illumos, Haiku and the others.
- a **trait** (`Trait`): any number per source, matched from what the dump
  records. Traits are shared across families: an initramfs generator
  (dracut, mkinitcpio, booster), a loader (systemd-boot, shim, Limine), a
  snapshot tool (snapper), ostree, and A3's storage layers (ZFS, md,
  multi-disk), which Linux, BSD and illumos share.

**The registry** (`families/__init__.py`) is the one table. It imports every
module in the package (pkgutil, sorted by file name), collects each module's
`FAMILY` and `TRAITS`, and refuses at import two modules of the same name,
or two families that both match one corpus dump (a unit test checks the
latter over every `holt/tests/corpus/*/src.json`). Adding a module adds it to
the table: there is no list to edit.
- `family_of(source, dump)`: the matching family with the highest `priority`
  (generic Linux has the lowest), else None.
- `hooks(source, dump)`: a `Hooks`, the family's hooks merged with those of
  every trait that matches. The generator, the rules, tier 0 and the sweep
  call only this; `generate()` makes it once per case.
- `PLANNED`: what isn't built, by the dump's OS (`freebsd: A4`, ...): a
  source with no family is SKIPPED, naming that piece (A9 reads it). A lane
  that adds its family drops its row in the same commit.

**What a module holds** (each optional; the default does nothing):

| Field | What it is | Merged |
|---|---|---|
| `name`, `piece` | its name, and the piece that built it (`A1`) | |
| `matches(source, dump)` | whether it applies | |
| `priority` | families only: the most specific wins | |
| `tables` | rule 2 rows, as `tables.TABLES`: `(TOOL, LOWEST_VERSION, {FIELD: allowed})` | a module's rows for a tool replace the generic rows for that tool; two modules with rows for one tool is an error |
| `packages` | `{TOOL: (package names in this family's dump)}` | added to `tables.PACKAGES` |
| `versions(dump)` | `{TOOL: version}` where the dump's packages don't say (NixOS, AerynOS) | the family's |
| `quirks` | `Quirk(name, why, check)`: `check(source, drifted, edits)` returns `[(RULE, WHY)]`, run after the three rules on every draw; a quirk is the source's own rule, as Alpine's mkinitfs rule is | all, in order |
| `ops(dump, weights)` | the op weights it allows (drops an op the source can't take) | in turn |
| `draws`, `edits` | new edit ops: `{op: (draw, weight, group)}` and `{op: EditOp}` (as `edits.OPS`), with `rule2_pairs(edit)` | added; an op name taken twice is an error |
| `config_texts(dump)` | rule 3: more of the source's configuration that names devices (Limine's `limine.conf`, NixOS's generated `grub.cfg`, BLS entries) | added to `dumpx.config_texts` |
| `number_patterns(dump, dk, p)` | rule 3: regexes that name partition P by number or path (`hdd(1:2)`, `/dev/ada0p2`, `c0t0d0s0`) | added |
| `names_by_number(dump, dk, p)` | rule 3, for what a regex can't say | added |
| `recipes`, `fs_can`, `content(dump, fstype)` | content drift: recipes added to `content.RECIPES`, what each filesystem can hold, and the recipe this source draws (None: no content drift) | the family's `content`; recipes and `fs_can` added |
| `known`, `steer(dump, drifted, edits, opts)` | its known findings (`{"fail-NNN": why}`) and how its draws steer around them (as `generate.KNOWN`) | all |
| `sweep(dump, opts)` | load's options this source can take (`--removable` where its loader has it) | in turn |
| `tier0` | `chroot` (dry runs in a chroot of the source's root), `guest` (in one booted source guest, batched: A4-A6), `plan` (load plan and invariants only) | the family's |

**Rules for lanes.**
- A lane owns its modules and their tests (`tests/test_family_NAME.py`). The
  shared files (the registry, `generate.KNOWN`, the README, this plan) are
  edited by one lane at a time, in small commits on main.
- Cases stay reproducible: a module draws only from the generator's `rng`,
  and never changes what an existing family draws. Each family has a golden
  test: the case hashes of seeds 0-199 over its corpus dumps, kept in
  `tests/golden/FAMILY.json`, rewritten only on purpose (a new
  `GENERATOR` version).
- Alpine is the first family (`families/alpine.py`); its cases are
  byte-identical to the ones drawn before the interface.

## Decisions (the user, 2026-11-14)

1. **Scope: every OS family** (Linux, BSD, illumos, Haiku, Redox, Sculpt OS).
2. **ZFS, md and multi-disk guests are sources**, with their drift edits.
3. **Haiku, Redox and Sculpt OS are all in.**
4. **Shape changes are phase 2**, not in this version.
5. **Size:** 1,000 tier 0, 20 tier 1 and 5 tier 2 (UEFI) per non-Alpine
   source; Alpine as BackShot2Alpine; 5 Thinkpad cases per round.
6. **A superset of BackShot2Alpine, one tool:** content drift and the option
   sweep on every source.
7. **CarpetBeater runs it every second round,** in BackShot2Alpine's place.
8. **The word runs early** (changed 2026-11-14; it first waited for all
   families): it runs what is built, lists unbuilt families as SKIPPED, and
   reports PARTIAL PASS until every family is in.
9. **Build in parallel lanes** after A0 and A1 (2026-11-14).

## Open

- A resumed `t2 batch` bs2 entry (`testvm/test2/t2lib/batchkinds.py`) restarts host queues with `BS2_WORD` (e754e95) but without `BS2_HOLT` or the reuse variables (`BS2_REUSE_*`, `BS2_REPO_BIND`), which the first start passes (found 2026-11-25, not fixed).
- Found by the first run (2026-11-25), each in its own session: anduinos2's
  snapshot-id finding (fixed in 798d7a3, proved 2026-11-25:
  testvm/backshot2/README.md); re-measuring
  the Slackware rows of `slim-sizes.tsv` (measured on FAIL cases); the bko-cache
  cap.  Resolved: zorin18's used space only seemed to grow when slimmed
  (7998 -> 8102 MiB): its root is nilfs2, whose df counts unreclaimed
  checkpoints; the slim step measures nilfs2 by du now (cec55d0), re-measured
  7568 -> 5247 MiB (6c5a11c).
- **The cargo umask 002 failure: resolved (907120c).**  plan_json's fake key
  files took the umask's mode, so under cmp-run.sh's umask 002 the blessed
  plans' key refusal read "mode 664"; the fixture pins them to 0644.
  bko-holt's check is unchanged.
- **The XFS-log fixer's ledger rows: left as they are (2026-11-25).**  chopin's
  ledger has 400 rows of run `fix-xfslog-chopin`.  All are tier 0 HARNESS for
  slackware15-uefi, written 14:20:39Z, with word BackShot2Alpine (the
  hand-run default; no `BS2_WORD`).  They are inert by design:
  - HARNESS never decides a cell (`bko.DECISIVE`, `plan.DECISIVE_VERDICTS`).
  - bs2's `done()` skips only on a PASS at the key.
  - `verdict_flips()` takes only another run's decisive rows as earlier ones.
  - Reports and the harness stop count only their own run's rows.
  - Walls come only from PASS and FAIL rows.
  - Their harness hashes predate later changes to bs2's files, so their keys
    can't recur.

  The ledger is append-only, with no way to retire a row.  Deleting the rows
  from chopin's file wouldn't remove them: the Mac's next pull keeps a row a
  host's file has lost in `chopin.lost.jsonl`, merges it into the view and
  warns about the loss (`ledger.pull_one()`).  So nothing was rewritten.
  Prevention landed in e754e95: bs2 writes rows only while a BackShot2 word
  runs (`BS2_WORD`) or with `BS2_LEDGER=1`, and says so on stderr otherwise.

- Whether ~15-16 h is acceptable for an every-second-CarpetBeater run, or
  whether non-Linux sources should draw fewer tier 1 cases (10) to keep it to
  one night. To decide after A0's and A2's measured times.
- Phase 2's shape changes: a spec of their own, after the first BackShot2All
  passes.
