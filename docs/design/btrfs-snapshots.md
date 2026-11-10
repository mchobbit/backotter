# bko-holt load: btrfs snapshots that share their extents

Status: **approved 2026-11-10** (approach A, the chain forward with
`--clone-from`, `--exact` always verifying, as recommended).  Built:
`holt/src/load/shared.rs` and its use in `commands.rs`; rustic_core
0.13.0+backotter.8 / rustic 0.11.4+backotter.9 `restore --exact` and
`--clone-from` (`crates/core/src/commands/restore/exact.rs`); tests
`testvm/compare/exact-rt.sh` and HoltTest1's `HT_SOURCE_PREP` with
`testvm/holttest1d/prep/tumbleweed-snapper.sh`.  Follow-up to
BackShot1's openSUSE Tumbleweed pass (run
opensuse-tumbleweed-20261108-20261110-165711, commit 81922b2).

## The problem

The backup holds a whole machine as the archiver saw it: `/` and everything
mounted below it.  On openSUSE (and on any system that uses snapper with
`/.snapshots` mounted), the snapshots are subvolumes nested in the mounted
`@/.snapshots`, so the backup holds each of them as a full tree at
`/.snapshots/N/snapshot`.  The repository deduplicates them.  The restore does
not.

What `load` does today, on Tumbleweed (root `@/.snapshots/1/snapshot` at `/`,
`@/.snapshots` at `/.snapshots`):

1. `format` makes every subvolume in the source's ID order.  A snapshot is
   made as a `btrfs subvolume snapshot` of its origin, which is still empty
   at that point.
2. `restore` runs one `rustic-bko restore` of the whole tree.  It writes each
   snapshot's files one by one into its subvolume, so each one takes the full
   5.8 GiB, and `snapper list` shows 5.83 GiB exclusive for each.
3. The root is restored twice: once at `/` and again at
   `/.snapshots/1/snapshot`, because that path is the same subvolume seen
   through `/.snapshots`.  The second pass collides with the first, which
   gave 21,850 `AlreadyExists` symlink warnings in
   `/var/log/bko-holt/load.log`.
4. `boot` takes longer for each snapshot (291 s, then 393 s).  Its atime walk
   (`atimes_cmd`, run twice) stats every path under the target, every
   snapshot included.
5. The restore needs room for every snapshot in full.  A machine with many
   snapshots can run out of space where the source never did.

## The approaches

| | (A) Snapshot, then restore the changes | (B) Reflink copy, then restore the changes | (C) Dedupe afterwards |
|---|---|---|---|
| How | After the main restore, each snapshot is made as a `btrfs subvolume snapshot` of a subvolume already restored, then made to match its backup by an in-place restore | Snapshots are made empty in `format`, as today; each is filled with `cp -a --reflink=always` from a restored one, then made to match its backup | Restore as today, then share identical extents (`FIDEDUPERANGE`) |
| Data written per snapshot | Only what differs | Only what differs | All of it, then freed |
| Metadata per snapshot | Shared, except the leaves that differ | Every inode, dirent and extent ref is new: about 0.5 KiB per file, about 100–150 MiB on Tumbleweed | All of it, never shared |
| `snapper list` exclusive | Close to the source's (tens of MiB) | Metadata plus the changes (about 150 MiB+) | Metadata (about 150 MiB+) |
| Peak space | Live data plus the changes | Live data plus the changes, plus the metadata | Every snapshot in full: the same failure as today |
| Time per snapshot | About 20 s (read and hash the existing tree) | Clone plus walk, about 60 s | Write it all, then read it twice |
| Subvolume IDs | The source's if snapshots come after every other subvolume (the usual case); otherwise new IDs | The source's, as today | The source's, as today |
| `parent_uuid` | Each snapshot is a snapshot of the one before it in its family (below) | As today | As today |
| Changes to rustic-bko | `--exact` and `--clone-from` (below) | `--exact` (below) | None, but a dedupe tool, or code for it in bko-holt |

**Recommendation: (A).**  It is the only approach whose result looks like the
source's: snapshots that share metadata as well as data.  It writes the least,
needs the least room, and is the fastest.  It costs two things: a snapshot's ID
can change when other subvolumes were made after it, and its `parent_uuid`
names the snapshot before it instead of the live root.  As far as I know,
snapper, grub2-snapper-plugin, grub-btrfs and limine-snapper-sync all work by
path and read neither value; the build checks this in their sources.  fstab
never names a snapper snapshot.  The times in the table are estimates; the
rehearsal measures them.  (B) is the fallback if exact IDs and parents matter more than
metadata sharing.  (C) still runs out of space where today's restore does,
so it is rejected.  So are btrfs send streams in the backup: they don't fit
rustic-bko's file-level format, and no other filesystem could restore them.

Whichever approach is chosen, the fix for point 3 is the same: the main
restore leaves out every path where a mounted subvolume shows up a second
time.

## Design (approach A)

### Words

- **Alias**: a path in the backup where a subvolume mounted somewhere else
  shows up again, because it is nested in another mounted subvolume of the
  same filesystem, and no mount covers that path.  Tumbleweed:
  `/.snapshots/1/snapshot`, which is `/`.  The same rule as `compare.py`'s
  `btrfs_aliases()`, computed from the mounts `load` itself makes (fstab, with
  a mount that names no `subvol=` or `subvolid=` resolved to the default
  subvolume).
- **Backup path** of an unmounted subvolume: where the backup holds its
  contents.  That is the mount point of the deepest mounted subvolume of the
  same filesystem that contains it, plus the rest of its path, provided no
  other mount covers that path.  `@/.snapshots/2/snapshot`, under
  `@/.snapshots` mounted at `/.snapshots`, has the backup path
  `/.snapshots/2/snapshot`.  A subvolume with no backup path isn't in the
  backup, and is handled as today.
- **Shared snapshot**: an unmounted subvolume with a backup path that either
  - has a `parent_uuid` naming a subvolume in the layout, or
  - sits in a snapper slot, `X/.snapshots/<N>/snapshot`.

  The second case covers snapper's original root (`1/snapshot`, which has
  no parent) once a rollback has left it unmounted.  A subvolume nested
  inside a shared snapshot rules that snapshot out (it falls back to today's
  handling, with a note).  So does any shared snapshot whose base can't be
  found.
- **Family**: the shared snapshots in one snapper `.snapshots` directory; for
  any other shared snapshot, those with the same origin (`parent_uuid`).
- **Base** of a shared snapshot: the subvolume it is snapshotted from before
  its changes are restored.  In order of preference:
  1. the family member before it, in ID order (consecutive snapper snapshots
     differ least);
  2. its origin, if that is restored before it (mounted, or an earlier shared
     snapshot);
  3. for a snapper slot, the subvolume mounted at `X`, the directory that
     holds the `.snapshots`.

  ID order is creation order.  For snapper it is the same order as the
  snapshots' numbers and their `ogen`.
- **Clone source**: the restored tree of the family's live subvolume (for
  snapper, the subvolume mounted at `X`).  It is passed to each snapshot's
  restore as `--clone-from`, below.

### Which way the chain runs

A chain from the oldest snapshot forward has one weakness when it starts
from the live root.  A file added after snapshot *j* that is still in the
live root isn't in snapshot *j*−1, so restoring snapshot *j* writes it again
instead of sharing the live root's copy.  On Tumbleweed that means every
package updated since the oldest snapshot.  There are three ways out:

1. **Oldest snapshot in full, chain forward, live root last, made from its
   ancestor** (the BackShot1 session's proposal).  Every file is shared.
   But the live root is mounted at `/` before the restore, and its ID comes
   before the snapshots.  Making it last would mean restoring every
   subvolume through the top level, mounting afterwards, and changing the
   live root's ID.  That rebuilds the stage order for a gain that (3) gets
   too.
2. **Newest first, from the live root, chain backward.**  Every file is
   shared too, but the snapshots are created newest first, so their IDs come
   out in reverse order.
3. **Chain forward from the live root, with `--clone-from` the live root**
   (chosen).  A file that differs from the base but whose contents equal a
   file in the live root is cloned (`FICLONE`) from that file, not written.
   The match uses the content lists in the backup, so nothing is read to
   find it.  That shares whole files, which is what snapper snapshots differ
   by: package files.  IDs stay in order, and the stages don't change.

What (3) can't share is a file that is in neither the base nor the live
root, but is in several snapshots, partly rewritten in each.  Only the first
copy has its blocks shared with later ones.  That is rare outside databases
and VM images, which snapper's default configs leave out of `/`.

### What changes, stage by stage

`format`, for a btrfs with shared snapshots:
- Makes every other subvolume as today, in ID order, with placeholders for
  the gaps.  A shared snapshot's ID is a gap only when a later subvolume
  needs the IDs below it burnt.
- Doesn't enable quotas.  Quotas are switched on after the restore (below),
  so the qgroup accounting doesn't slow down the restore or the snapshot
  creation.
- A mounted subvolume whose origin is a shared snapshot (the root after
  `snapper rollback`) is made as a plain subvolume.  The note says that it
  was a snapshot of `@/.snapshots/K/snapshot`, which is restored later.

`restore`:
1. Writes `RUN_DIR/restore.globs`: one `!ALIAS` line per alias, and one
   `!BACKUP_PATH/*` line per shared snapshot.  The main restore then leaves
   each shared snapshot's directory empty if the backup has it, and absent
   if the backup doesn't.
2. Runs the main restore as today, with `--glob-file RUN_DIR/restore.globs`.
3. Mounts the top level (`subvolid=5`) with `noatime`, so that reading the
   existing trees doesn't dirty their inodes.
4. For each shared snapshot, in source ID order, one step (a `sh -c`, shown
   in full in the plan):
   - If the main restore left its directory (so the backup has it):
     `rmdir`, then `btrfs subvolume snapshot TOP/BASE TOP/PATH`, then
     `rustic-bko … restore --numeric-id --exact --clone-from
     LIVE_BACKUP_PATH=TOP/LIVE SNAP:BACKUP_PATH TOP/PATH`.
   - If not: `btrfs subvolume create TOP/PATH`, empty as today, and a note
     at run time that the backup didn't have it.
   - First, placeholders up to the source's ID, when the next free ID is
     below it, so the ID comes out the same.  When the next free ID is
     already past it, the snapshot gets the next free ID, and the plan's
     note says `ID 271 on the source, 284 here`.  The next free ID is known
     when the plan is made: it is the highest ID left after `format`, plus
     one.  `format`'s top-level mount is the filesystem's only mount, and
     when btrfs mounts it again it counts from the highest ID that's left.
     Each step checks the ID it got, and says so in the log if it differs
     from the plan's.
5. Deletes the placeholders made in this stage, then runs `btrfs subvolume
   sync`.
6. Sets `ro=true` on the read-only shared snapshots.  Nothing writes to them
   after this point.
7. Quotas, if the source had them: `quota enable`, the higher-level qgroups
   (snapper's `1/0`), the assignments and the limits.  A shared snapshot's
   `0/ID` uses its ID here, not the source's.  Then one `quota rescan -w`.
8. The atime list (`atimes before`), pruning the read-only shared snapshots.
   They can't change, so they don't need to be walked.

`boot`:
- Its atime walks prune the same paths.  This takes away point 4's growth.
- Its read-only loop skips the shared snapshots, which are already
  read-only.

snapper's `info.xml` files sit in `@/.snapshots/N/`, outside the snapshot
subvolumes, so the main restore puts them back as before.  Nothing special
is needed for them.

Without `--repo` (the administrator restores by hand), nothing changes:
snapshots are made in `format` as today.  The plan's note says which paths to
leave out of the restore: the aliases.

New option: `--btrfs-snapshots shared|copy`, default `shared`.  `copy` is
today's behaviour, aliases left out.  It's for a stick whose rustic-bko
predates `--exact`, or a layout this design gets wrong.  With `shared`, the
`restore` stage first checks that `rustic-bko restore --help` lists
`--exact`.  If it doesn't, the stage refuses before writing anything, and says
to use `--btrfs-snapshots copy`.

### rustic-bko: `restore --exact`

Today a restore over a tree that is already there has three problems.  It
writes into a file in place, which corrupts the file's other hard links.  It
sets every node's metadata unconditionally: each `chown` or `utimensat`
dirties the inode, so btrfs copies every metadata leaf and nothing stays
shared.  And it leaves the destination directory's own metadata alone unless
the target is fresh.  `--exact` (rustic_core 0.13.0+backotter.8, rustic
0.11.4+backotter.N) makes the destination exactly the node tree, writing
only what differs:

- It implies `--delete` and `--verify-existing`.  A file whose size and mtime
  match is still read and compared, blob by blob.  At 1–2 GB/s that is about
  5–10 s for Tumbleweed's 5.8 GiB, which is what makes "same size and mtime,
  different contents" safe.
- An existing regular file with more than one link is first replaced by a
  reflink clone of itself (`FICLONE` to a temporary file in the same
  directory, then renamed over).  The clone still shares its extents, and no
  write can reach the other names.  The metadata pass then makes the links
  the node tree has.  Where reflinks aren't supported, the file is removed
  instead.
- Metadata is set only where it differs from what's there: owner, mode,
  atime and mtime, extended attributes (the SELinux label among them), ACLs
  and flags.  A flag that would block a change (`i`, `a`) comes off first,
  and is put back if the node has it.
- The destination directory itself gets the node's metadata.  Here it is the
  snapshot subvolume's root directory.
- Symlinks, special files, and entries of another type: replaced, as the fork
  already does.  An identical symlink stays.

`--clone-from SNAPPATH=DIR` (usable with `--exact` only).  Before the
restore, rustic-bko walks the backup's tree at `SNAPPATH`, which is metadata
only, from the cache.  It maps each regular file's content list to its path
under `DIR`.  A file the restore would write whose content list is in the
map is instead cloned whole from `DIR/PATH` with `FICLONE`, then given its
own metadata.  `DIR` must hold `SNAPPATH` as restored, unchanged since:
here, the live root right after the main restore, before `boot`.  A clone
that fails (for example a file that has changed size) falls back to writing
the file.

None of this needs ordering in rustic-bko.  The order lives in bko-holt's
plan.

restic-bko is unchanged.  `load` restores only with rustic-bko, and this flag
is restore-side, so node parity and cross-restores aren't affected.

## Known differences from the source

These belong in `compare.py`, `bs1-manifest` and the layout comparison as
expected differences, not failures:

- A shared snapshot's `parent_uuid` names its base, not the source's origin.
  The first in each family usually keeps the source's origin.
- A shared snapshot's ID differs from the source's when a subvolume after it
  was made in `format`.  The plan notes which.
- After a rollback, the mounted root has no parent.
- ctime, as for every restored file.

## Onto another filesystem

`load` always restores btrfs onto btrfs today.  A future way of restoring
onto XFS or ext4 (a `--reformat`) can't keep snapshots.  Its plan must say
plainly that it drops them, name them, and leave them out of the restore,
rather than restore them as directories.

## Tests

- **Unit tests** (`commands.rs`): aliases, backup paths, which subvolumes
  are shared snapshots, bases, and ID assignment.  Synthetic layouts cover:
  plain Tumbleweed; several snapshots; a rollback (root `5/snapshot`, ID
  after the snapshots, with the unmounted `1/snapshot` from before it); a
  subvolume made after a snapshot (so the ID moves); a `/home` snapper
  config; a qgroup limit on a snapshot; read-only and read-write snapshots;
  a subvolume nested in a snapshot (falls back); `--btrfs-snapshots copy`;
  no `--repo`.
- **Golden plans**: `tumbleweed/plan.txt` changes (its snapshot 2 is shared,
  ID 265, exact).  A new corpus entry, `tumbleweed-snapper`: a real dump of
  the Tumbleweed guest after several snapshots and a `snapper rollback`.
  It is collected as `holt/tests/corpus/README.md` says, and blessed with
  `BKO_HOLT_GOLDEN=bless`.
- **rustic-bko**: tests in rustic_core for `--exact`: hard links broken,
  then remade; same size and mtime but different contents gets fixed; a
  restore over an identical tree changes nothing (no write and no inode
  change, checked by ctime); destination metadata; immutable files;
  `--clone-from` clones a file added since the base (checked with
  `FIEMAP`: the same physical extents as the source file) and writes one
  whose content list isn't in the map.
- **QEMU rehearsal** on Chopin or Cinqcent, under `cmp-run.sh` reservations,
  in RAM: the Tumbleweed guest in a `--keep` overlay, given about five
  snapshots (zypper changes and `snapper create`) and a rollback, then a
  HoltTest1-style round trip.  It passes when:
  - `snapper list` on the restored guest shows each snapshot's exclusive
    size within a small factor of the source's (tens of MiB, not GiB);
  - `btrfs qgroup show` agrees;
  - `load.log` has no `AlreadyExists`;
  - the file comparison passes, with the differences listed above;
  - the restored guest boots, and `snapper rollback` to a restored snapshot
    still works.
- **BackShot1** on the Thinkpad's Tumbleweed install, if the user wants it
  after the QEMU pass.

## Open questions for the user

1. Approach A, accepting new IDs in the rare case and `parent_uuid` pointing
   at the previous snapshot?  Or approach B, which keeps both but leaves about
   150 MiB+ exclusive per snapshot?
2. `--exact` always verifies existing contents (about 5–10 s per snapshot on
   Tumbleweed).  Keep that, or trust size and mtime by default?  Trusting is
   faster, and slightly less safe.
3. A snapper slot with no parent (the original root, after a rollback) is
   treated as a shared snapshot of the live root.  On restore it gains a
   parent it didn't have.  Acceptable?
4. Which way the chain runs: forward with `--clone-from` (recommended), or
   newest first (every file shared, but snapshot IDs in reverse order)?
