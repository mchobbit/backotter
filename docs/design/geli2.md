# GELI, round 2: a BIOS guest and a UFS guest (approved)

2026-11-17. The user asked for a spec to improve GELI support in bko-holt,
items 1 and 2 of that answer: test guests for the layouts that only unit
tests and scenarios cover today. It builds on docs/design/geli.md (approved
2026-11-14, G1-G8 done). The user approved it on 2026-11-17 with E1-E7 as
recommended. est. marks an estimate. *Check* marks a fact to
settle on the guest before the code that depends on it. H1-H8 are the steps,
E1-E7 the decisions.

## Summary

- Two new FreeBSD 15.1 guests, both GELI:
  - **`freebsd15-geli-bios`**: what FreeBSD's installer makes when you take
    the defaults and answer "Encrypt Disks? yes" and "Encrypt Swap? yes".
    BIOS + GPT, ZFS root pool on GELI, onetime-encrypted swap.
  - **`freebsd15-geli-ufs`**: a hand-built layout that covers what nothing
    else does. UFS root on GELI with GELIBOOT, a second key slot, and a data
    provider unlocked by a key file alone with authentication (HMAC/SHA256).
- Both join HoltTest1BSD and HoltTest2 2BSD, with a corpus dump and a golden
  plan each. ImgTest2, SticTest and the rescue image don't change (E5).
- bko-holt's code should need no new feature. GELI round 1 wrote it all and
  tested it with scenarios. What these guests find is fixed as it comes,
  through High-effort subagents with unit tests.
- ~3 d (est.), most of it the UFS guest's install and the first HoltTest1
  runs.

## Why

What has actually run end to end (HoltTest1 on Chopin and Cinqcent,
HoltTest2, `+img`) is one layout: UEFI, a ZFS root pool on GELI, passphrase,
key slot 0, no authentication, plain swap. geli.md's D1 left the rest to
unit tests and scenarios:

| Feature | Covered by now | Covered by |
|---|---|---|
| BIOS loader (gptzfsboot) asking for the passphrase | nothing | `-bios` |
| onetime swap (`.eli` in fstab, nothing for load to do) | scenario | `-bios` |
| the installer's own default GELI layout | nothing | `-bios` |
| UFS on GELI (`newfs` on `PROV.eli`, loader.efi reading UFS on GELI) | scenario | `-ufs` |
| key slot 1 (`--key-file PROV:1=FILE`, D4's refusal) | scenario | `-ufs` |
| a key-file-only provider (`-P -K`, setkey after the restore) | scenario | `-ufs` |
| authentication (`-a HMAC/SHA256`, the dd that fills the provider) | scenario | `-ufs` |
| rc.conf `geli_devices` / `geli_PROV_flags` read by dump | unit test | `-ufs` |

The BIOS loader matters on its own: it is where the 15-key keyboard buffer
problem was found (console-boot.py types one key at a time since then), and
gptzfsboot's prompt is a different program from loader.efi's.

## The guests

Both are built by `testvm/make-freebsd.sh` from the 15.1 disc1 ISO, like
`freebsd15-geli`, with their passphrases in the guest dir on each host
(mode 600), never in the repo.

### freebsd15-geli-bios

`make-freebsd.sh --bios-geli 15.1 zfs vms/freebsd15-geli-bios`, a new flag
beside `--uefi-geli`. Host name `fbsd15-gelib`.

| | |
|---|---|
| Disk | `vtbd0`, GPT, BIOS |
| p1 | `freebsd-boot`, 512 KiB, gptzfsboot |
| p2 | `freebsd-swap`, 2 GiB; fstab `/dev/vtbd0p2.eli none swap sw` (onetime) |
| p3 | `freebsd-zfs`, `geli init -bg -e AES-XTS -l 256 -s 4096`, pool `zroot` on `vtbd0p3.eli` |
| Files | `console-passphrase` |

- bsdinstall's zfsboot does it all: `ZFSBOOT_GELI_ENCRYPTION=1`,
  `ZFSBOOT_SWAP_ENCRYPTION=1`, `ZFSBOOT_BOOT_TYPE=BIOS`. The passphrase
  goes in through the same `bsddialog-geli` shim. *Check:* the variable
  names in 15.1's zfsboot, and that the BIOS path still writes a
  `/boot/efi`-less layout with no separate `/boot` pool (it hasn't since
  FreeBSD 12).
- *Check:* the prompt gptzfsboot prints (`GELI Passphrase for disk0p3:` is
  expected) against console-boot.py's regexp.

### freebsd15-geli-ufs

`make-freebsd.sh --geli-ufs 15.1 ufs vms/freebsd15-geli-ufs`. bsdinstall's
guided UFS has no "Encrypt" choice, so `installerconfig` gets a preamble
that partitions and makes the providers itself, then lets the scripted
install continue on the mounted tree. Host name `fbsd15-gelu`. UEFI (E2).

| | |
|---|---|
| Disk | `vtbd0`, GPT, UEFI (`--uefi --no-secure-boot`) |
| p1 | `efi`, 260 MiB, `/boot/efi` |
| p2 | `freebsd-swap`, 1 GiB, plain (the BIOS guest covers onetime swap) |
| p3 | `freebsd-ufs`, `geli init -bg -e AES-XTS -l 256 -s 4096`, passphrase in slot 0; `geli setkey -n 1` gives slot 1 a second passphrase; `newfs -U -j` on `vtbd0p3.eli`, mounted `/` |
| p4 | `freebsd-ufs`, 1 GiB: `geli init -a HMAC/SHA256 -e AES-XTS -l 128 -P -K /etc/geli/p4.key`, keyfile only; `newfs -U` on `vtbd0p4.eli`, mounted `/data` |
| rc.conf | `geli_devices="vtbd0p4"`, `geli_vtbd0p4_flags="-p -k /etc/geli/p4.key"` |
| fstab | `/dev/vtbd0p4.eli /data ufs rw 2 2` |
| Files | `console-passphrase` (slot 0), `console-passphrase-slot1` |

- p4's key file lives on the encrypted root, so the loader never sees it and
  rc.d/geli attaches p4 after `/` is mounted. That is the common way to do a
  key-file data provider, and it is the path where load makes the provider
  with a temporary key and `setkey`s the restored key file in after the
  restore (geli.md, load step 5).
- `/data` gets a few known files, so judge's comparison sees the provider's
  contents come back.
- *Check:* gptboot (BIOS) is not used here; loader.efi reads UFS-on-GELI with
  GELIBOOT on 15.1. Same loader as `freebsd15-geli`, a different file system
  under it.
- *Check:* whether 15.1's `geli init -a` on a 1 GiB provider takes long
  enough (it writes every sector) to matter in the install and in load.

### What H1 and H2 found

Both built on Chopin from the 15.1 disc1 ISO on 2026-11-17, in about a
minute each, and booted on a throwaway snapshot through console-boot.py.
Neither passphrase nor p4's key file is in `install-serial.log` or the
build log (checked by content, not printed).

`freebsd15-geli-bios`:
- 15.1's zfsboot has the three variables as named. Its comments say GELI
  "automatically enables ZFSBOOT_BOOT_POOL"; it doesn't any more, so with
  no boot pool it runs `geli init -bg -e AES-XTS -J - -l 256 -s 4096` and
  `geli attach -j -`, as for `freebsd15-geli`.
- The layout is as the table says: p1 `gptboot0` freebsd-boot 512 KiB
  (pmbr + gptzfsboot), p2 `swap0` 2 GiB, p3 `zfs0` 14 GiB; no ESP, no
  `/boot/efi`, no boot pool. fstab has only
  `/dev/vtbd0p2.eli none swap sw 0 0`; loader.conf `aesni_load`,
  `geom_eli_load`, `zfs_load` and the two `kern.geom.label` lines; rc.conf no
  `geli_*`; `/var/backups` empty.
- gptzfsboot prints `GELI Passphrase for disk0p3: `, then `Calculating GELI
  Decryption Key for disk0p3: 2782171 iterations...`. It asks once: the
  loader and kernel reuse the key. console-boot.py's regexp matches as it is.
- `geli list`: `vtbd0p3.eli` BOOT, GELIBOOT, AUTORESIZE, UsedKey 0;
  `vtbd0p2.eli` ONETIME, W-DETACH, W-OPEN, AUTORESIZE, AES-XTS 128, sector
  4096 (swapon's defaults); `swapinfo` lists `/dev/vtbd0p2.eli`.
- vm-update took it to 15.1-RELEASE-p7 (Chopin only, `--hosts ''`).

`freebsd15-geli-ufs`:
- The partitioning is `freebsd/geli-ufs.sh`, sourced by installerconfig's
  preamble from the media's `/etc/bko-geli-ufs.sh`. It stands in for the
  `bsdinstall scriptedpart` and `mount` steps through a shell function, as
  the ZFS branch already does for `umount`. The passphrases and key file
  reach the installer as files on the media (`/etc/bko-geli/`), passed to
  geli with `-J` and `-K`.
- bsdinstall's own steps do the rest from the fstab it writes: `mount`,
  bootconfig (loader.efi onto the ESP as `efi/freebsd/loader.efi` and
  `efi/boot/bootx64.efi`, because fstab has `/boot/efi`), and config, which
  adds `loader.conf.*` and `rc.conf.*` fragments (`geom_eli_load`,
  `aesni_load`; `geli_devices`, `geli_vtbd0p4_flags`).
- loader.efi reads UFS on GELI with GELIBOOT on 15.1: the same prompt as
  `freebsd15-geli` (`GELI Passphrase for disk0p3: `, 2991056 iterations),
  and the kernel mounts `/dev/vtbd0p3.eli` as the root.
- rc.d/geli attaches p4 with `/etc/geli/p4.key` at boot, and `/data` mounts.
  The key file is 64 random bytes. The build also keeps a copy as
  `geli-p4.key` (mode 600) in the guest dir, beside the two passphrase
  files, for H5's leak scan; HoltTest still reads the key from the source's
  root.
- `geli init -a` costs nothing that matters: the dd that fills p4 took
  4.3 s (538 MB at 125 MB/s). With authentication and GELI's default
  512-byte sector (the table gives p4 no `-s`), the provider is 513 MiB of
  the 1026 MiB partition, because each sector carries its HMAC. That's kept
  as the table has it.
- `geli list` adds RW-DETACH to both providers, a runtime flag that isn't in
  the metadata. dump reads the metadata sector, so it doesn't see it.
- p3 is `newfs -O 2 -U -j` (dumpfs), p4 `newfs -O 2 -U`.
- The first vm-update failed in pkg (`No SRV record found for the repo
  'FreeBSD-base'` on its second `pkg update`, after the first had read the
  catalogue). That was the network: the Mac's own DNS failed at the same
  minute. It was retried on Cinqcent (Chopin's RAM-disk line was held up by
  another session's job) and took the guest to 15.1-RELEASE-p7. The
  updated folder was then copied back to Chopin, which has to have it
  before a publish.

Also: `rebuild-one.sh` has the two recipes, and doesn't copy the slot-1
passphrase or the key file into a rebuild.

### What H3 to H6 found

- **A bko-holt bug, fixed.** `dumpfs -m` prints `-s` in 512-byte units, but
  FreeBSD's newfs counts the device's own sectors. On the 4096-byte-sector
  root provider, load's `newfs ... -s 28819448` failed (`maximum file system
  size is 3602431`), so a UFS on a GELI provider made with `-s 4096` (or on
  a 4Kn disk) could never be restored. other/load.rs's `other_newfs` now
  converts `-s` to the device's sectors (the GELI record's `sectorsize`,
  else the disk's logical sector size), rounds down, and says so in the
  plan. Unit tests are in other/load.rs and other/geli.rs. No golden plan
  changed: no corpus dump had UFS on a device with large sectors.
- **A harness bug, fixed.** The first ht1-bsd.sh gave the key-file provider
  `--key-file vtbd0p4=PASSPHRASE` too. Now a provider keyed by `keyfile`
  gets no `--key-file`, so load takes the temporary-key-and-`setkey` path,
  as the spec says.
- **The BIOS rescuer asks too.** gptzfsboot in the freebsd15-ufs rescuer
  probes the source's disk and asks `GELI Passphrase for disk1p3:` (round
  1's "not yet seen"). ht1-bsd.sh already boots it through console-boot.py.
- **HoltTest1 passes on two hosts each** (pick-host's order, after the vm-store
  publish put the guests in the small hosts' caches):
  - `freebsd15-geli-bios`: `geli2a` on cinqcent (holt-bin's bko-holt) and
    `geli2c` on cage (this tree's).
  - `freebsd15-geli-ufs`: `geli2b` on cinqcent and `geli2c` on copland (this
    tree's bko-holt, sha256 `132c892f05ba...`). Its first run, `geli2a`,
    found both bugs above.

  Each run checks the restored disk booting alone with slot 0's passphrase
  typed at the loader. On the -ufs guest it also checks the slot-1 refusal,
  slot 1 opening the restored provider from the rescuer, `/data` attached
  by rc.d/geli and mounted with the source's three files (SHA-256), and a
  dump diff of 0. On the -bios guest it checks that `swapinfo` lists
  `vtbd0p2.eli`. ram-sizes.tsv: 3/4 and 4/4.
- **The corpus.** Both dumps and their golden plans are in.
  - The -ufs guest's plan is refused (exit 4), because corpus plans take no
    `--key-file` and its source uses slot 1. Its stages still show the
    whole build.
  - collect-dumps.sh's leak scan doesn't read `console-passphrase-slot1` or
    `geli-p4.key`, so a separate scan looked for all four secrets: both
    guests' slot-0 passphrases, the slot-1 one and the key file, as they
    are, as UTF-16LE, in hex and in base64. It ran over the dumps, plans and
    index rows, and over both guests' HoltTest1 run directories, and found
    none.
- **Test2.** Both guests are in `holt.families.BSD`, holt/spec.json and
  guest/spec.json (with `console-boot.py`; `established: false`, so
  `new-guest.sh` runs as well), with estimates rows. Counts went from 79 to
  81 sources, 88 to 90 holt cells and 84 to 86 spec entries (plan.py's class
  check, README, tests, the p2 test world and its goldens). The spec said
  "class B2 beside `freebsd15-geli`", but B2 is a SticTest class and E5
  keeps SticTest as it is, so neither guest joined `stic`. HoltTest2 has no
  classes; its family is BSD (2BSD).

## bko-holt

No new feature is planned. These are what the guests should exercise, and
where a fix would go if one is needed:

- **dump:** `keyed_by` `passphrase` with `keys: [0, 1]` for p3, `keyfile` with
  `keyfiles: ["/etc/geli/p4.key"]` for p4, `aalgo: "HMAC/SHA256"`, ONETIME for
  the BIOS guest's swap (seen in `geli list` while attached, and in fstab).
- **load:**
  - the `-ufs` guest needs `--key-file vtbd0p3=FILE --key-file
    vtbd0p3:1=FILE1`. Without the `:1`, load refuses (D4); HoltTest checks
    that refusal once, from the plan, before the real load.
  - p4 is made with a temporary key, filled by the auth dd, newfs'd,
    restored, then `geli setkey -n 0 -K /mnt/etc/geli/p4.key` with `-P`.
  - the BIOS guest's swap: the partition only, nothing to report.
- **judge.py:** compares the `geli` fields, as now. For p4 it compares
  `keyed_by` and `keyfiles`, not the key file's bytes; the key file is
  restored by the archive like any file, and the provider opening with it
  at boot is the proof.

## Tests

**HoltTest1** (ht1-bsd.sh), both guests, on two hosts picked by
`testvm/pick-host` (E6), under cmp-run.sh with a WEAR_TAG:
- `backup`: as `freebsd15-geli`. The `-ufs` guest's mount script also
  attaches p4 with its key file, read from the source's root once p3 is
  attached (`geli attach -r -p -k /mnt/etc/geli/p4.key vtbd0p4`).
- `rebuild`: the `--key-file`s above. The `-ufs` run also runs `load --plan`
  without `:1` first and needs the refusal.
- `verify`: the restored disk boots alone through console-boot.py, which
  types slot 0's passphrase. SSH answering means the loader took it. Then on
  the restored guest:
  - `geli list` shows `vtbd0p4.eli` attached and `/data` mounted, with the
    known files;
  - `swapinfo` shows `vtbd0p2.eli` (the BIOS guest);
  - slot 1 can't be tried on the booted guest, whose root is attached
    already, so verify tries it from the rescuer before the boot, read-only
    (`geli attach -r -n 1 -j FILE1 vtbd0p3`, then `geli detach`).
- `ram-sizes.tsv` rows from the first runs.

**HoltTest2:** both join 2BSD and class B2 beside `freebsd15-geli`
(design-classes.md), as the 58th and 59th sources (the count is whatever
main has then). Each gets a corpus dump, collected under a throwaway overlay
and leak-scanned for both passphrases' and p4's key file's bytes, and a golden
plan blessed on Chopin. holt/spec.json and guest/spec.json entries carry
`"harness": ["testvm/console-boot.py"]` and the `-ufs` guest's firmware.

**Unit tests:** dump's rc.conf parsing gets the guest's exact lines, and the
scenarios gain the guests' layouts if they differ from what scenarios.rs has.

**vm-store:** both guests are published once they pass, like the round-1
guests, so the other hosts take them from the cache.

## Steps

| | What | Where | est. |
|---|---|---|---|
| H1 | `--bios-geli`: zfsboot variables, build `freebsd15-geli-bios`, settle its *checks* | testvm/make-freebsd.sh, Chopin | 0.25 d |
| H2 | `--geli-ufs`: installerconfig preamble, build `freebsd15-geli-ufs`, settle its *checks* | testvm/make-freebsd.sh, testvm/freebsd/, Chopin | 0.75 d |
| H3 | ht1-bsd.sh: slot-1 key file, p4 attach in the mount script, the refusal check, verify's extra checks | testvm/holttest1d (subagent, High) | 0.25 d |
| H4 | HoltTest1 both guests on two hosts, fixing until PASS (bko-holt fixes via High subagents, with unit tests) | farm | 1 d |
| H5 | corpus dumps, leak scans, golden plans, Test2 entries; HoltTest2 for the two new cells | holt/tests/corpus, testvm/test2 | 0.5 d |
| H6 | vm-store publish; HoltTest1BSD lists, README, holttest1d memory line | testvm | 0.1 d |
| H7 | geli.md: a pointer to this round; corpus README | docs | 0.05 d |
| H8 | commit on main when asked | | |

H1 and H2 are independent and can run side by side. H3 waits for H2's
layout. H4 needs both guests and H3.

## Decisions

- **E1. Two guests, not one.** Two guests, as above (*recommended*: the
  installer's default layout is the one users will have, and it shouldn't
  wait on a hand-built one). Or one combined guest (BIOS, UFS root, onetime
  swap, slot 1, key-file auth provider): cheaper by ~0.5 d, but it's a layout
  no installer makes, and it leaves BIOS+ZFS on GELI untested.
- **E2. The UFS guest's firmware.** UEFI (*recommended*: BIOS is covered by
  the other guest, and UEFI is what a new laptop has). Or BIOS, which would
  test gptboot's GELI prompt as well, at the cost of loader.efi on UFS.
- **E3. Slot 1's passphrase** lives in `console-passphrase-slot1` beside the
  slot-0 file on each host (*recommended*). Or a key file in slot 1 instead
  of a passphrase, which tests `PROV:1=FILE` with a key file (load already
  handles both the same way).
- **E4. p4's key file** on the encrypted root, attached by rc.d/geli
  (*recommended*). Or loaded by the loader (`geli_vtbd0p4_keyfile0_load`),
  which needs an unencrypted `/boot` and belongs with geli.md's D7
  (unattended boot) instead.
- **E5. ImgTest2.** No new `+img` cells (*recommended*: the image's `geli`
  is already exercised by `freebsd15-geli+img`, and the panel's cost grows
  per cell). Or add `freebsd15-geli-ufs+img` (14 cells).
- **E6. Hosts.** Build both on Chopin, then HoltTest1 on two hosts
  `testvm/pick-host` gives, as every job now (*recommended*). Or Chopin and
  Cinqcent as in round 1.
- **E7. BackShot1 on real hardware** (item 3 of that answer) and the
  unattended restored boot (geli.md D7) stay out of this round
  (*recommended*: they need the Thinkpad on FreeBSD and a plan of their own).
  Or plan them here as H9-H10.
