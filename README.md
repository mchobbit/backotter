# BackOtter

BackOtter backs up whole machines and rebuilds them.  It ships as a rescue
image (Linux, the BSDs, illumos and Haiku) carrying patched restic
(`restic-bko`) and rustic (`rustic-bko`), and `bko-holt`, which records a
machine's disk layout and rebuilds it from that record (`holt/README.md`).

- `holt/`: bko-holt, in Rust
- `stick/`: building the rescue images
- `docs/design/`: the design documents
- `testvm/`: the test guests and the round trips run on them

## Reporting a bug

On the machine where it went wrong (the rescue stick, or the system you were
backing up), run:

```
sudo bko-holt report-bug
```

It writes one plain-text file, `bko-report-DATE.txt`: the versions of
bko-holt, restic-bko, rustic-bko, the image, the OS and kernel; the system's
`bko-holt dump`; the last `bko-holt load` run with its plan and Problems; and
bko-holt's logs.  Add a backup's or restore's error output with
`--stderr FILE` (`restic-bko backup ... 2> errors.txt`).

Issues are public, so the file is redacted by default: hostnames, UUIDs,
serial numbers, labels, user names, key-file paths, MAC addresses and the
machine-id become pseudonyms (`host-1`, `label2`), the same one everywhere,
while sizes, offsets, feature flags and the device layout stay.  The command
prints what it replaced and what it kept.  Read the file, then attach it to a
[new issue](https://github.com/mchobbit/backotter/issues/new/choose).

## License

BackOtter's own code (`holt/`, `stick/`, `testvm/`) and its documents are
free software under the GNU General Public License, version 3 or later
(`LICENSE`).

The patched archivers keep their upstream licenses, so the patches can go
back upstream: restic-bko is BSD-2-Clause like restic, and rustic-bko is
MIT or Apache-2.0 like rustic.  The rescue images also carry their
operating systems' own software under those systems' licenses.
