# FreeBSD GELI support in bko-holt (approved)

2026-11-14. The user asked for it ("Add GELI support to bko-holt. Do it."),
and approved this design the same day with the recommended decisions D1-D7. est. marks an estimate.
*Check* marks a fact to settle against FreeBSD's source or the first guest
before the code that depends on it. G1-G8 are the steps, D1-D7 the
decisions.

## Summary

- `bko-holt dump` finds GELI providers on FreeBSD (HardenedBSD and GhostBSD
  share the code) and records how each was made. It records what `geli init`
  needs to make the same provider again, and never any key material.
- `bko-holt load` makes each provider again with `geli init`, attaches it,
  and builds the pool or UFS on the `.eli` device. The boot flags come back
  too, so the loader asks for the passphrase again. The passphrase comes from
  `--key-file NAME=FILE` (unattended) or is typed at a prompt (attended), as
  for LUKS.
- `freebsd15-geli` becomes a HoltTest1BSD and HoltTest2 2BSD source, with a
  corpus dump and a golden plan. ht1-bsd.sh types its passphrase at the
  loader over the serial console (`console-boot.py`, which already knows
  FreeBSD's prompt). The guest also joins ImgTest2's FreeBSD panel as a
  `+img` cell. SticTest doesn't change.
- ~3.5 d (est.); most of it is load and the first HoltTest1 runs.

## Why

- `freebsd15-geli` is the one guest bko can't restore: it was the only
  failure of 49 in r4all (4649d3b), and HoltTest2 leaves it out as a source
  (test2.md decision 11, [holt (b)]).
- "Encrypt Disks?" is one question in FreeBSD's installer, so a FreeBSD
  laptop with root on ZFS will often be on GELI.
- Today `dump` sees a partition it can't read and a pool whose member is no
  partition, and `load` would build the pool on the bare partition. The
  restored system would boot unencrypted, if it booted at all, with
  `geom_eli_load` and a stale `/var/backups/*.eli` restored next to it.

## What the guest is

From `vms/freebsd15-geli/install-serial.log` (bsdinstall's zfsboot, 15.1, on
Chopin):

| | |
|---|---|
| Disk | `vtbd0`, GPT, UEFI (`--uefi --no-secure-boot`) |
| p1 | `efi`, 260 MiB, label `efiboot0`, `/boot/efi` (msdosfs) |
| p2 | `freebsd-swap`, 2 GiB, label `swap0`; fstab `/dev/vtbd0p2 none swap sw`. **Not** encrypted: this guest has no onetime swap |
| p3 | `freebsd-zfs`, label `zfs0`: `geli init -bg -e AES-XTS -J - -l 256 -s 4096 vtbd0p3`, then `geli attach -j - vtbd0p3` |
| Pool | `zpool create -o altroot=/mnt -O compression=on -O atime=off -m none -f zroot vtbd0p3.eli` |
| Loader | `loader.conf`: `aesni_load="YES"`, `geom_eli_load="YES"`, `kern.geom.label.disk_ident.enable="0"`, `kern.geom.label.gptid.enable="0"` |
| Prompt | `GELI Passphrase for disk0p3: `, then `Calculating GELI Decryption Key for disk0p3: 3031038 iterations...` |
| Backup | `geli init` wrote `/var/backups/vtbd0p3.eli` (the metadata, with the encrypted master key), on the pool |

The passphrase is in `vms/freebsd15-geli/console-passphrase` (mode 600) on
each host. vm-update, vm-lib and SticTest1 already type it from there.

## What G1 found

On the guest (15.1-RELEASE-p6, booted on Chopin on a throwaway snapshot with
the passphrase typed by console-boot.py, 2026-11-14):

- **The metadata sector** (the provider's last 512-byte sector),
  little-endian and packed:

  | Offset | Size | Field | Guest |
  |---|---|---|---|
  | 0 | 16 | magic, `GEOM::ELI` padded with NULs | |
  | 16 | 4 | version | 7 |
  | 20 | 4 | flags | 0x282 = BOOT (0x2), GELIBOOT (0x80), AUTORESIZE (0x200) |
  | 24 | 2 | ealgo | 22 (AES-XTS) |
  | 26 | 2 | keylen | 256 |
  | 28 | 2 | aalgo | 0 (none) |
  | 30 | 8 | provsize | 14757658624, the consumer's size |
  | 38 | 4 | sectorsize | 4096 |
  | 42 | 1 | keys | 0x01 |
  | 43 | 4 | iterations | 3031038 |
  | 47 | | salt, then the encrypted master keys and the MD5 hash: never read | |

  This decode matches `geli dump` field for field.
- **`geli list`** prints, for each attached provider: `Geom name: vtbd0p3.eli`,
  `Flags: BOOT, GELIBOOT, AUTORESIZE`, `UsedKey: 0`, and the consumer's name
  (`vtbd0p3`) under `Consumers:`. The provider's sector size is 4096; the
  consumer's is 512.
- **The pool** shows `vtbd0p3.eli` as its only vdev. The swap
  (`/dev/vtbd0p2`) isn't encrypted. Neither rc.conf nor loader.conf has
  `geli_*` lines: loader.conf has only `aesni_load` and `geom_eli_load`.
- **`/var/backups` is empty** on the installed system. bsdinstall wrote
  `vtbd0p3.eli` into the installer's own `/var/backups`. D5 still applies to
  a system where `geli init` was run on it.
- **`geom_eli.ko`** is loaded by the loader (`kldstat`).
- **`geli`'s usage** confirms the flags of `init` (`-bdgPRTv`, `-a`, `-B`,
  `-e`, `-i`, `-J`, `-K`, `-l`, `-s`, `-V`), `attach` (`-Cdprv`, `-n`, `-j`,
  `-k`) and `setkey`.

## bko-holt dump

**The metadata, read by bko-holt itself.** GELI keeps its metadata in the
provider's last sector (`struct g_eli_metadata`, sys/geom/eli/g_eli.h). A new
probe, `holt/src/probe/geli.rs`, finds the magic `GEOM::ELI` there and decodes
only these fields: version, flags, ealgo, keylen, aalgo, provsize,
sectorsize, keys (the mask of the two key slots in use) and iterations. It
never reads the salt, the encrypted master keys or the MD5 hash into memory
it keeps, so nothing secret can reach the report, a bug report or the corpus.
Reading the sector ourselves means it also works for providers that aren't
attached, and offline. It is the same parser on every platform, which can
be unit-tested with a sample sector. We don't parse `geli dump`, which
prints the salt and master key next to the rest. *Check:* field offsets and
the flag bits (ONETIME, BOOT, AUTH, NODELETE, GELIBOOT, GELIDISPLAYPASS,
AUTORESIZE) against g_eli.h of 15.1, and our decode against `geli dump` on
the guest. For the algorithms, we record both the number and its name
(AES-XTS, AES-CBC, Camellia-CBC; HMAC/SHA256 and so on).

**What's attached.** `geli list` gives each attached provider's `.eli` name,
its consumer and whether it is ONETIME. Only those three are kept from it.

**In the report.** The partition's content is `geli` with the fields above,
plus:

```json
"geli": {"version": 7, "flags": ["BOOT", "GELIBOOT", "AUTORESIZE"],
         "ealgo": "AES-XTS", "keylen": 256, "aalgo": null,
         "sectorsize": 4096, "keys": [0], "iterations": 3031038,
         "provider": "vtbd0p3.eli", "attached": true,
         "content": [ ...probe of /dev/vtbd0p3.eli... ],
         "keyed_by": "passphrase", "keyfiles": []}
```

- **Inside.** When it's attached, `/dev/PROV.eli` is probed like a partition.
  A ZFS member there becomes one of `zfs_section`'s members under the `.eli`
  name, so the pool's vdev `vtbd0p3.eli` resolves to `vtbd0p3` in the layout.
  A UFS there gets the same `newfs`/`tunefs` record a plain UFS partition
  gets, from `dumpfs -m /dev/PROV.eli`.
- **Passphrase or key file.** The metadata doesn't say. So `dump` reads where
  FreeBSD keeps it: `loader.conf`'s `geli_PROV_keyfileN_load/_type/_name`
  (key files the loader loads for a boot provider) and `rc.conf`'s
  `geli_devices` and `geli_PROV_flags` (`-p`, `-k FILE` for rc.d/geli).
  `keyed_by` is `passphrase`, `keyfile` or `both`, and `keyfiles` lists their
  paths. The paths are configuration, not secrets (as crypttab's key field
  is), and `redact.rs` already treats key-file paths as identifying.
- **Names.** `resolve()` (other/freebsd.rs) gains `/dev/X.eli`: the partition
  `X` resolves to, with `eli: true`. That covers fstab, `mount -p` and
  `swapctl -l` (`/dev/vtbd0p2.eli` for onetime swap, `/dev/gpt/zfs0.eli`).
- **Onetime swap** is an fstab entry whose device ends in `.eli`. rc's
  `swapon` attaches it with a fresh random key at each boot, so the
  partition has no GELI metadata. It's recorded as fstab already records it,
  and `swaps` shows it as `onetime`.

## bko-holt load

New commands in the FreeBSD stages (other/load.rs, with the ZFS and newfs
steps in load/commands.rs that already take this system's device names):

1. **After partitioning, before any pool or newfs:** for each `geli` content
   in the layout, on the target partition:
   `geli init [-b] [-g] [-d] [-T] [-a AALGO] -e EALGO -l KEYLEN -s SECTORSIZE -i ITERATIONS -V VERSION (-J FILE | -P -K FILE | prompt) -B none DEV`.
   - `-b`, `-g` (GELIBOOT), `-d` and `-T` (NODELETE) come from the flags, and
     `-R` when AUTORESIZE is off.
   - `-B none` means `geli init` writes no metadata backup into the rescue
     system (see step 4).
   - Then `geli attach` with the same key, and a closer, `geli detach`, that
     runs after the pool's export.
2. **The pool and UFS go on `/dev/TARGETp3.eli`:** the vdev map gives the
   `.eli` node for a member that was one on the source.
3. **Authenticated providers** (`-a`): after attach,
   `dd if=/dev/zero of=DEV.eli bs=1m`, because each sector's checksum must be
   written before it can be read (geli(8)). The plan says this, and how long
   it takes for that size.
4. **After the restore:** if the restored tree has `/var/backups/SRC.eli`,
   `geli backup TARGETDEV /mnt/bko-target/var/backups/TARGET.eli` writes it
   again for the new provider, and a stale `SRC.eli` of another name is
   removed (D5). The old file describes a master key that no longer exists.
   `geli restore` from it would destroy the new provider.
5. **Key files** (keyed by `keyfile` or `both`): like LUKS with a crypttab
   key file. `init` and `attach` use a temporary random key under
   `RUN_DIR/keys`. After the restore, `geli setkey -n 0 -K RESTORED_KEYFILE
   [-J FILE | -P]` puts in the restored key file. `--key-file` overrides the
   temporary key.
6. **Onetime swap:** only the partition is made. The `.eli` in fstab does the
   rest at boot, so there is nothing to do and nothing to report.
7. **Refusals.** Load refuses, in the plan and before any write:
   - a key slot 1 that `--key-file` doesn't name (D4);
   - a version, ealgo or aalgo this rescue system's `geli` can't make, found
     with `geli init` on a memory disk at plan time (*check* whether that's
     cheap enough, or a table by FreeBSD release is enough);
   - a target sector smaller than the provider's sectorsize. A bigger one
     is fine, and so is GELI's own 4096 on a 512-byte disk.

**`--key-file NAME=FILE`**, the same option as for LUKS:

- `NAME` is the source's provider (`vtbd0p3`), or its GPT label path
  (`gpt/zfs0`). `--plan` lists the names it accepts.
- `FILE` holds the passphrase with no final newline, as for LUKS. It's
  passed as `-J FILE` (`-j` for attach).
- `NAME:1=FILE` sets key slot 1 too, when the source used it.

**Attended:** with no `--key-file`, `geli init` asks twice for the new
passphrase on the terminal ("Enter new passphrase", "Reenter new
passphrase") and `attach` asks again. exec.rs already gives commands the
terminal's stdin, as cryptsetup has it. The plan says "GELI on vtbd0p3: asks
for the new passphrase". Three prompts is clumsy, so *check* whether load
should read the passphrase once itself (no echo) and pass it on through a
`RUN_DIR/keys` file of mode 600, removed when load ends. D3 recommends that.

## Passphrase entry on the rescue image

- **FreeBSD's image** (`stick/freebsd`) already carries `geli`
  (checks.sh's tools list, manifest.sh's `tool:geli`) and loads
  `geom_eli` when it's first used. It needs no change. G7 checks the module
  loads on the image (`kldstat -m g_eli` after `geli load`), and the image's
  checks gain that line.
- **Attended:** load's prompt above, on the image's console.
- **Unattended:** as BackShot1's LUKS runs: the caller passes `--key-file
  PROV=FILE`, and `FILE` is a passphrase file on the backup drive
  (`backshot1/keys/disk-passphrase`; bs1-common's `layout_keys` gains GELI
  providers next to LUKS volumes). The restored provider then has the same
  passphrase as the source.
- **Booting the restored system** with nobody at the keyboard (the loader's
  prompt answered from a key file on a USB drive) isn't part of this.
  FreeBSD's loader only reads key files from an unencrypted `/boot`, and a
  BackShot1 FreeBSD run hasn't been planned. That's open as a follow-up,
  noted under D7.

## Tests

**Unit tests** (`holt/`, `cargo test` on Cinqcent):
- the metadata decoder: a sample sector made with the guest's values, every
  flag, both versions we support, a corrupt magic, a truncated provider, and
  a check that the salt's and master key's bytes never appear in the JSON;
- `resolve()` with `.eli` names;
- rc.conf and loader.conf key-file parsing;
- `scenarios.rs` FreeBSD layouts whose plans show the `geli init` and
  `attach` argv: root pool on GELI (`-bg`), UFS on GELI, keyfile data
  provider, onetime swap (no geli command), authenticated (the dd), slot 1
  refused, and `--key-file` (`-J`) against the prompt.

**HoltTest1** (ht1-bsd.sh):
- `vm_up` boots the source and the restored disks through
  `testvm/console-boot.py` when the guest's dir has `console-passphrase`, as
  ht1.sh does for Linux (its prompt regexp already matches
  `GELI Passphrase for disk0p3:`). The rescuer boots as now.
- `backup`: the passphrase goes to the rescuer, in its RAM-backed `/tmp`,
  mode 600, and is removed with the rescuer. `mount_script` emits
  `geli attach -r -j FILE NODE` for each `geli` content before the pool
  import. `umount_script` emits `geli detach` after the export.
- `rebuild`: `load --key-file vtbd0p3=FILE`, so the restored provider has the
  source's passphrase. `verify` attaches the restored disk the same way,
  then boots it alone through console-boot.py. SSH answering means the
  loader took the passphrase and the kernel mounted the pool.
- `judge.py` compares the `geli` fields of the source's and the restore's
  dumps. All of them should match; `provsize` may differ by the partition's
  own rounding, which judge already allows for partitions.
- `ram-sizes.tsv`'s row is measured on the first run.
- `freebsd15-geli` joins HoltTest1BSD (README, ht1-queue.sh's family
  lists).

**HoltTest2:**
- `freebsd15-geli` becomes the 57th source, in 2BSD and in class B2 next to
  freebsd15 (design-classes.md: "geli ≡ freebsd15" stays true for the pool,
  and the provider is what B2 now adds).
- Corpus: a dump of the live guest, booted through console-boot.py under a
  throwaway overlay, collected and leak-scanned as cachyos-zfs's was
  (1b980cd), plus a scan that the passphrase's bytes aren't in it.
- Golden plan: blessed on Chopin.
- Updates: the holt/spec.json and guest/spec.json entries, plan.py's
  counts, and test2.md decision 11's note.

**ImgTest2:** a `+img` cell for `freebsd15-geli` on the FreeBSD panel
(12 → 13 cells, D6). That way the image's own `geli` makes the provider.

**SticTest:** unchanged; GELI is below the pool, which the archivers never
see.

## Steps

| | What | Where | est. |
|---|---|---|---|
| G1 | Settle the *checks* on the guest: g_eli.h offsets and flags against `geli dump`/`geli list` output; the rescue image's `geli` and module; what the three prompts look like | Chopin, freebsd15-geli overlay | 0.25 d |
| G2 | probe/geli.rs, resolve `.eli`, dump's `geli` record, key-file sources; unit tests | holt/ (subagent, High) | 0.5 d |
| G3 | load: init/attach/detach, `.eli` vdevs and newfs, auth dd, key files and setkey, `/var/backups` refresh, refusals, `--key-file` names, the one-prompt passphrase (D3); scenarios | holt/ (subagent, High) | 1 d |
| G4 | ht1-bsd.sh: console-boot.py boots, geli attach in mount/umount scripts, `--key-file`, judge fields | testvm/holttest1d | 0.25 d |
| G5 | HoltTest1 `freebsd15-geli` on Chopin and Cinqcent under cmp-run.sh, fixing until PASS, restored system booted with the typed passphrase | both hosts | 0.75 d |
| G6 | Corpus dump, golden plan, Test2 entries; HoltTest2 2BSD | holt/tests/corpus, testvm/test2 | 0.5 d |
| G7 | Rescue image: checks.sh's module line; ImgTest2 `+img` cell (run with the next image round, not a rebuild of its own) | stick/freebsd, testvm/test2 | 0.25 d |
| G8 | bs1-common `layout_keys` for GELI (no run) | testvm/realworld/backshot1 | 0.1 d |

G2 and G3 touch other/freebsd.rs, other/load.rs and load/commands.rs, which
other sessions are editing too. Each brief says which functions it may
change, and the diffs are kept small and local.

## Decisions

- **D1. Scope.** Dump and load cover everything above: a root pool or UFS on
  GELI, keyfile data providers, onetime swap, and authenticated providers.
  Only `freebsd15-geli` (root pool, passphrase) is a test guest; the rest is
  covered by unit tests and scenarios. *Recommend:* yes. Or also build a
  second guest, UFS on GELI with onetime swap (+1 d).
- **D2. Iterations.** Use the source's `-i` (*recommended*: as LUKS keeps
  its KDF cost, and the loader's wait stays the same). Or `-i 0`, which
  calibrates again on the rescue machine.
- **D3. One prompt.** When attended, load asks once and gives `geli` the
  passphrase in a temporary mode-600 file (*recommended*). Or let `geli`
  prompt three times, as cryptsetup's own prompts do for LUKS.
- **D4. Key slot 1.** If the source uses it, load refuses unless
  `--key-file PROV:1=FILE` is given (*recommended*: the restored system
  would otherwise silently lose a way in). Or recreate slot 0 and warn.
- **D5. `/var/backups/*.eli`.** Write it again for the new provider and
  remove the stale one (*recommended*: a stale one can destroy the new
  provider). HoltTest's mtree comparison allows those two paths. Or leave
  the restored file as it was.
- **D6. ImgTest2's FreeBSD panel** gains `freebsd15-geli +img`
  (*recommended*). Or HoltTest2 only.
- **D7. Unattended boot of the restored system** (the loader reading a key
  file from a USB drive). Not part of this (*recommended*). It goes with a
  future BackShot1 FreeBSD run.
