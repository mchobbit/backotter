# vm-store: the test guests' images, content-addressed

`testvm/vm-store` keeps versions of the guests in `vms/` as immutable,
sha256-named objects, so that a host without a full `vms/` can fetch the
exact version a test needs.  The contract is `docs/design/image-store.md`;
this is how to use it.  The logic is `vmstore.py`; the tests are in `tests/`.

## Roles

| Role | Where | Does |
|---|---|---|
| mirror | Chopin, `/bkoscratch/bko-store` | the only publisher; pushes to Bigfoot; is also Chopin's cache |
| master | Bigfoot, `/media/DantePool/bt/BkoStore` (`VM_STORE_BIGFOOT`) | the copy of record; written only by Chopin's push, by rename from `/media/DantePool/bt/.bko-staging`; vm-store never runs there |
| cache | Cinqcent, Copland and Cage (Czerny once racked), `/bkoscratch/bko-cache` | fetches from Chopin with `ensure`, evicts with `gc` |

A root's role is its `store.json`'s, set by `init`.  `testvm/hosts.json`
records the same split as host roles (`store-mirror` on Chopin, `cache` on
the others; testvm/HOSTS.md), for the test scripts; vm-store itself goes by
`store.json` only.

Only Chopin talks to Bigfoot: push, `init --bigfoot` and `verify --where
bigfoot` run there, and no other host has a key on Bigfoot.  With Chopin
down, a cache's `ensure` uses the versions it already holds (as `--offline`,
with a warning for `@latest`).  `BkoStore` sits at bt's top level, which the
weekly backup copies to the three gmk servers without `--del`, so those
copies are append-only; the staging dot-dir is never backed up.  Test-guest
secrets (`id_ed25519` and the like) are stored 0400 and go to those backups
too.

## Commands

```
vm-store init /bkoscratch/bko-store --role mirror        # once, on Chopin
vm-store init --bigfoot                                  # once, from Chopin (after S0)
vm-store init /bkoscratch/bko-cache --role cache [--cap 100G --floor 100G]

vm-store publish GUEST...            # Chopin: store vms/GUEST's current state
vm-store publish --changed --push    # weekly (by hand; there is no scheduler)
vm-store publish --changed --stateless G1,G2   # and these state-less guests too (opt-in)
vm-store push [--now]                # Chopin to Bigfoot, in VM_STORE_WINDOW unless --now

vm-store ensure GUEST[@VID]          # prints the guest folder made of that version
vm-store fetch GUEST...              # the same, printing nothing (prefetch)
vm-store link GUEST[@VID]... | --all # caches: point vms/GUEST at that version (default latest)
vm-store gc [--dry-run]              # caches: make room, least recently ensured first
vm-store list [GUEST] [--source chopin]
vm-store status
vm-store verify [--where local|bigfoot]
```

Exit status: 0 ok, 1 failure, 2 usage, 3 busy (try later), 4 no room.
Progress goes to stderr; stdout carries only the result.

## Versions

A guest with `image-state` gets the version `g` + the first 16 hex of its
generation; one without gets `i` + 16 hex of t2's `img:` sum of its disks.
Publishing a version again changes nothing (but finishes a publish that died
before moving `latest`).  `latest` moves to a new `g` version with more
updates, to the first `g` version of an `i` one's disks, or with
`--set-latest`; a new `i` version (a rebuild) never moves it on its own.

Publish the changed guests weekly (`publish --changed --push`), not after
every vm-update round: until deltas (M2) every new version is a full copy
kept forever on Bigfoot and on each gmk server.  Nothing schedules it.

A guest without `image-state` is published by name, and `--changed` skips
it unless `--stateless G` names it (repeatable, or comma-separated).  An
opted-in guest is always copied and hashed, since its `i` VID is known only
then; an unchanged one is dropped at commit ("already published").  Its
`latest` follows `vms/G` as with `--set-latest`, so a rebuild of a guest in
development becomes its latest (by name, without `--stateless`, a rebuild
leaves `latest` alone).  Which guests to opt in is the user's call.

**The floor** (default 200G on the mirror): by default every copy needs
its allocated size, reflinked or not, and publish refuses (exit 4) when that
would take free space below the floor.  Opt in with
`VM_STORE_FLOOR_REFLINK=1` to count what the copies write now instead: with
`VM_STORE_COPY=auto`, the guest's disks on the store's file system and a
probe file there reflinking, a copy writes no new blocks, so it needs no
room and goes ahead even below the floor (with a warning).  The blocks a
reflinked object comes to hold alone are taken later, as vm-update rewrites
the guest's own image, and nothing checks those against the floor.  A
reflink that fails at copy time is checked again as a full copy before it
is made.  Whether to opt in, or lower Chopin's floor instead, is the user's
call.

Publish takes vm-update's fleet lock (exit 3 while a round or update runs),
and refuses a guest with an unfinished update, a disk with a backing file,
or one open for writing (a writable boot, vm-mount without `--ro`).  Readers
(`--ram`, `--snapshot`, compare, ht1) don't stop it.

## The guest folder

`ensure` prints `ROOT/live/GUEST/VID/GUEST/`: the disks as relative links to
the read-only objects (under their own names, so `vm-run-options`' `--disk`
names still work), the sidecars as copies with their modes (`id_ed25519`
0600), a synthesized `image-state`, and `.vm-store.json` naming the version.

vm-run refuses to boot a store object writable, and vm-mount refuses to mount
one without `--ro`, even as root: boot it with `--ram` or `--snapshot`.
qcow2-punch.py refuses it outright.

## Cache hosts' `vms/`: link

On a cache host without a full `vms/` (the small hosts, Copland and Cage:
hosts.json's hosts without the `fleet` role), each `$BKO_WORK/vms/G`
is a symlink to `ROOT/live/G/VID/G`, so the test scripts find the guest
where they always do.  `vm-store link G` ensures G's latest (fetching it
from Chopin) and re-points `vms/G` at it with one rename (`--offline`: only
what is cached; `G@VID`: that version, a rollback; `--dry-run`: say what it
would do).  `link --all` re-points every `vms/` link that already points
into this store, hand-made ones and dangling ones included; it adds none.
Run it on each cache host after a publish.  Link:

- runs on a cache root only (exit 1 on the mirror);
- never replaces a real `vms/G` folder, nor a link that points anywhere
  but this store's `live/G/` (exit 1, before fetching anything);
- refuses (exit 3, after the fetch) while a VM of G runs: a QEMU program
  holding one of the current version's disk objects (rule a's lock test),
  or a `compare.py` / `compare-freebsd.sh` of G (Linux `/proc`);
- does nothing to a link that already names the version.

gc keeps whatever `vms/` uses (rule f): every object of a version a `vms/G`
link resolves into, and every object a link inside a real `vms/G` resolves
to; it never removes a `live/G/VID` a link points into.  So a cache's
linked guests are never evicted however long ago they were ensured, and
once `link` moves `vms/G` on, the old version ages out like any other.
A dir under `vms/` gc can't read (a sudo `compare.py`'s 0700 one) may hide
a link: gc says "can't read" and keeps what was ensured in the last 7 days,
as for an unreadable overlay scan dir.  Size a cache's cap for every linked guest plus room for one new version.

This is an interim step of M3 (`docs/design/image-store.md` section 3): the
per-job pins (`VM_STORE_PIN`, `vm_store_dir`) are not built.

## Environment

| Variable | Default |
|---|---|
| `VM_STORE_ROOT` | `/bkoscratch/bko-store` if it exists, else `/bkoscratch/bko-cache` |
| `VM_STORE_SOURCES` | `chopin=m@chopin.local:/bkoscratch/bko-store` (in order; a bare path is a local source; on Chopin only, `bigfoot=m@bigfoot.local:/media/DantePool/bt/BkoStore` may be added for a test cache root) |
| `VM_STORE_BIGFOOT`, `VM_STORE_BIGFOOT_STAGE` | `/media/DantePool/bt/BkoStore`, `/media/DantePool/bt/.bko-staging` |
| `VM_STORE_BIGFOOT_HOST` | `m@bigfoot.local` (empty: Bigfoot's side runs here on local paths, for tests) |
| `VM_STORE_BWLIMIT_BIGFOOT` | `40000` (KiB/s) |
| `VM_STORE_WINDOW` | `00:30-07:30` (push only) |
| `VM_STORE_COPY` | `auto` (reflink, else a sparse copy); `plain` |
| `VM_STORE_FLOOR_REFLINK` | `0` (publish counts every copy's allocated size against the floor); `1` counts a reflinked copy as no new space |
| `VM_STORE_SCAN` | `/mnt/bko-ram /dev/shm /bkoscratch` (gc's search for overlays) |
| `VM_STORE_MIN_AGE` | `24h` (gc keeps what was ensured more recently) |
| `VM_STORE_CAP`, `VM_STORE_FLOOR` | the root's `store.json` |
| `BKO_WORK` | `~/backotter-work` (`vms/` is under it: link's links, gc's rule f) |

## Tests

```
cd testvm/store && python3 -B -m unittest tests.test_vmstore
bash testvm/store/tests/vm-run-guard.sh
```

Both use temp dirs only.  Without qemu-img (the Mac) the disks are
hand-built qcow2 headers, and the real-image cases are skipped; under a bash
older than 4 the guard script tests only `in_store`.
