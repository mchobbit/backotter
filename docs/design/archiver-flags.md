# The flags BackOtter passes its archivers

BackOtter backs up and restores with its own builds of restic and rustic
(restic-bko and rustic-bko, the `backotter` branches of mchobbit/restic,
mchobbit/rustic_core and mchobbit/rustic).  Both forks keep their upstream's
defaults, so that someone who uses them outside BackOtter is never worse off
than with stock restic or rustic.  BackOtter gets the behaviour it needs by
passing flags, on every call.  The test harness (`testvm/compare`,
`testvm/stictest1`, `stick/build-rustic-native.sh`'s smoke test) passes the
same flags, so what it checks is what BackOtter runs.

## Backup

| Tool | Flag | Default without it |
|---|---|---|
| restic-bko | `--with-atime` | the mtime is stored as the atime (stock restic) |
| rustic-bko | `--set-atime yes` | the mtime is stored as the atime (stock rustic) |

Why: a bare-metal restore gives back the machine as it was, and some
software reads atimes: mail readers tell new mail from read mail by them,
`tmpwatch`-style cleaners and `systemd-tmpfiles` age files by them, and
`find -atime` in admins' own scripts does too.  The cost is accepted: every
backup after the files were read writes new trees (about 2.3 MB per backup
with nothing changed on Chopin, against 0.5 KB with the mtime), and the
snapshots record when each file was last read.

restic's `--with-atime` is a boolean flag: `--with-atime` or
`--with-atime=true`.  `--with-atime false`, without the `=`, makes `false` a
path to back up.

## Restore

| Tool | Flag | Default without it |
|---|---|---|
| restic-bko | none | numeric owners (stock restic's default) |
| rustic-bko | `--numeric-id` | owners mapped by user and group name (stock rustic) |

Why: BackOtter restores a machine onto its own disks, usually from the rescue
stick, whose `/etc/passwd` and `/etc/group` are the stick's, not the
machine's.  Mapping by name there would give files to the stick's accounts.
The numbers are the machine's own, and the restored `/etc/passwd` names them
again.  BackOtter never passes restic's `--ownership-by-name` or rustic's
`--by-name`.

## Snapshots BackOtter wrote

Both forks write `+backotter` in a snapshot's `program_version`.  In such a
snapshot a missing attribute means it was zero or absent at backup time (no
file flags, no project ID, no hole marker), and a restore sets it so.  In a
snapshot from stock restic or rustic a missing attribute means unknown, and
the forks leave the destination's value alone, as stock does.  BackOtter
needs no flag for this: every snapshot it restores is its own.

Cross-tool restores (a restic-bko snapshot restored with rustic-bko, and back)
keep working and give the same nodes; BackOtter itself never mixes the tools
in one repository.
