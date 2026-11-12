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
| cache | Cinqcent and later the M900s, `/bkoscratch/bko-cache` | fetches from Chopin with `ensure`, evicts with `gc` |

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
vm-store push [--now]                # Chopin to Bigfoot, in VM_STORE_WINDOW unless --now

vm-store ensure GUEST[@VID]          # prints the guest folder made of that version
vm-store fetch GUEST...              # the same, printing nothing (prefetch)
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
| `VM_STORE_SCAN` | `/mnt/bko-ram /dev/shm /bkoscratch` (gc's search for overlays) |
| `VM_STORE_MIN_AGE` | `24h` (gc keeps what was ensured more recently) |
| `VM_STORE_CAP`, `VM_STORE_FLOOR` | the root's `store.json` |
| `BKO_WORK` | `~/backotter-work` (`vms/` is under it) |

## Tests

```
cd testvm/store && python3 -B -m unittest tests.test_vmstore
bash testvm/store/tests/vm-run-guard.sh
```

Both use temp dirs only.  Without qemu-img (the Mac) the disks are
hand-built qcow2 headers, and the real-image cases are skipped; under a bash
older than 4 the guard script tests only `in_store`.
