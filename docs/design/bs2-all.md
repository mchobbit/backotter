# BackShot2All: all of BackShot2, every OS family, fresh (plan, agreed)

2026-11-14. Plan agreed with the user in conversation; nothing in it is built.
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
- **The word waits for all of it** (the user's choice): `BackShot2All`
  refuses to run until every family below is built. Meanwhile, `BackShot2
  SOURCE...` and `BackShot2Alpine` run whatever is built.
- **Size:** about 59 sources today. The QEMU tiers have ~1,250 cases, and a
  run is ~15-16 h on the four hosts (est.).
- **Build:** about 16 days (est.) before the first run.

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
| Other Linux, one disk | aerynos, arch, artix-dinit, artix-s6, cachyos, chimera, chimera-uefi, debian12, debian13, devuan6, endeavouros, endeavouros-grub, fedora44, fedora44-uefi, gentoo, gentoo-systemd, kinoite44, manjaro, mint22, mx25, nixos2605, omarchy, pop24, rocky10, rocky10-lvm, tumbleweed, ubuntu24, ubuntu26-luks, ubuntu26-uefi, void-musl, zorin18 | 31 |
| Linux ZFS, md, multi-disk | cachyos-zfs, chimera-zfs, debian13-zfs, debian13-stack, fedora44-raid, rocky10-raid5, ubuntu26-raid, ubuntu26-zfs, ubuntu26-zfsmirror | 9 |
| BSD | freebsd14, freebsd15, freebsd15-mirror, ghostbsd26, ghostbsd26-zfs, hardenedbsd15, dragonfly6, netbsd11, openbsd8, alpinebsd | 10 |
| illumos | omnios58, omnios58-mirror | 2 |
| Haiku, Redox, Sculpt OS | haiku6, redox, sculpt2610 | 3 |
| | | **59** |

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
| A1 | B11, grown: rule 2 tables and source quirks for the other Linux sources: dracut, mkinitcpio, booster, systemd-boot, shim, Limine, snapper, ostree (kinoite44), NixOS generations, Gentoo, Chimera. Plus rule 3's loader paths (Limine, systemd-boot entries, NixOS's generated GRUB) | 3 d |
| A2 | B12: tier 1 and 2 for those sources: source map, `SECS` per source, which cases go to tier 2 | 1 d |
| A3 | ZFS, md and multi-disk drift, shared by Linux, BSD and illumos: ashift, vdev partitions placed and sized, md metadata version and offsets, per-disk targets, member placement. Rule 3 for pool and array names | 2 d |
| A4 | BSD: rule 2 tables (gpart, newfs, bsdlabel and disklabel for OpenBSD and NetBSD, HAMMER2), tier 0 dry runs in a source guest, rule 3 (`/dev/ada0p2` in fstab, `vfs.root.mountfrom`, boot code) | 4 d |
| A5 | illumos: EFI and VTOC labels, ZFS root, the loader and bootadm, rule 3 | 1.5 d |
| A6 | Haiku, Redox, Sculpt OS: partition drift, BFS block size, their rule 3 | 1.5 d |
| A7 | Content recipes per filesystem | 1 d |
| A8 | B10: tier 3 glue, a case onto the Thinkpad through backshot1. Copy the library runs' `layout.json` and repositories from mybig512 to a host | 0.75 d |
| A9 | The `BackShot2All` word, its `plan`, the refusal while any family is unbuilt (naming it), its report | 0.5 d |
| | **≈ 16 d**, then a first run and its fix cycle | |

Order: A0, then Linux (A1, A2, A7), then A3, A4, A5, A6, then A8 and A9. Each
family can run under `BackShot2 SOURCE...` as soon as it's built.

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
8. **The word waits for all families:** it refuses to run until A1-A9 are
   built.

## Open

- Whether ~15-16 h is acceptable for an every-second-CarpetBeater run, or
  whether non-Linux sources should draw fewer tier 1 cases (10) to keep it to
  one night. To decide after A0's and A2's measured times.
- Phase 2's shape changes: a spec of their own, after the first BackShot2All
  passes.
