# Spec: bko image store (`testvm/vm-store`), final first cut

This spec was written read-only. Nothing was changed on any host, VM or file. On top of the surveys and critiques, I read only `testvm/vm-update` (the fleet and guest lock code, :398-410 and :1787-1890) and the header of `testvm/compare/cmp-run.sh`.

Labels used below:
- **[A…]** marks an assumption that the host tests check.
- **[D…]** marks a decision the user must make.

## 0. Facts this spec rests on

1. **sync-hosts.sh copies no images**, and it stays that way. Today guests move by vm-update overlays, by `swap-guests.sh` (on the hosts only) and by copies made by hand. The store replaces the hand copies first, and later the full `vms/` copy on new hosts.
2. **Images today:**
   - All 69 disks are standalone qcow2, with no backing file and the internal snapshot `fresh-install`.
   - They are changed in place by vm-update (commit, snapshot retake, punch) and by writable boots (for example the chimera dev jobs right now).
   - At the same generation the bytes differ per host. So the store takes **one host's bytes (Chopin's)** per version, and never compares hosts.
3. **Bigfoot backups** (`/usr/local/bin/backup-to-remote-hc4s`, weekly):
   - Top-level `bt/[0-9A-Za-z]*` is rsynced to gmkone, gmktwo and gmkthree **without `--del`**, and excludes `/Software`.
   - `bt/Software/` is rsynced **with `--del`**.
   - Dot-dirs at bt's top level are never backed up.
   - `bt/` is `root:root 755`. `bt/Software` is `m:video 2775`. Both are the one dataset DantePool/bt, so a rename between them is atomic.
4. **SSH:**
   - Chopin to Bigfoot works.
   - Cinqcent to Bigfoot is denied (publickey).
   - Chopin and Cinqcent reach each other both ways.
   - `/bkoscratch` is `m:m 755` on Chopin and Cinqcent, so no sudo is needed there.
5. **Tools:**
   - rsync is 3.5.0 on the hosts and 3.4.1 on Bigfoot, so `--fsync` is available.
   - qemu-img 10.0.13 is on the hosts only.
   - python 3.13 is everywhere.
   - b3sum is on no host, so hashing uses sha256.
6. **`ionice -c3` does nothing useful here.** Chopin's NVMe scheduler is `none`, and on Bigfoot ZFS issues the disk I/O from its own threads. The only real throttles are `--bwlimit`, one stream at a time, and a time window. `--bwlimit` caps rsync's wire bytes, which are zstd-compressed, not the logical or the on-disk write rate: in the 2026-11-13 push the logical rate reached 130 MB/s while the allocated on-disk rate happened to stay at 41-52 MB/s, near the design's 40 MB/s. `nice -n19` stays, for CPU (zstd, sha).
7. **vm-update locks** (vm-update:404-406, 1817-1890):
   - It takes `vms/G/.vm-update.lock` `LOCK_EX|LOCK_NB` and holds it for the life of the process.
   - It takes the fleet lock `vms/.vm-update.fleet.lock` on Chopin `LOCK_NB`: EX for a by-hand run, SH for a round's job. `--hold-fleet-lock` takes EX and then turns it to SH.
   - Real updates and `--finish` hold the fleet lock. `--try` and `--probe` hold only the guest lock.
8. **QEMU image locking** uses OFD locks: byte `100+bit` means "holds the permission" and byte `200+bit` means "does not share it". WRITE is bit 1.
   - So a writer holds byte **101**.
   - A backing-file reader holds byte **201** ("write not shared").
   - A new writer's open fails if anyone holds 201.

## 1. Layout

The same tree shape is used in every role, so there is one code path.

```
ROOT/
  store.json                     {"schema":1,"role":"master|mirror|cache","cap":"100G","floor":"100G"}
  objects/sha256-<64hex>.qcow2   disk objects, 0444, immutable
  objects/sha256-<64hex>.file    sidecar objects, 0400, immutable
  objects/.incoming/             staging on the same fs (rsync --partial-dir=.partial inside)
  guests/<G>/versions/<VID>.json         immutable manifest
  guests/<G>/versions/<VID>.json.sha256  its sha256
  guests/<G>/latest              "<VID> <manifest sha256>\n", rewritten by tmp+rename
  live/<G>/<VID>/objects -> ../../../objects     (not on Bigfoot)
  live/<G>/<VID>/<G>/            materialized guest dir (not on Bigfoot)
  used/<object>                  empty stamp; mtime = last ensure (caches only)
  lock                           flock: fetch, materialize, gc
  .publish.lock  .push.lock      Chopin only
```

There is no catalog or index file: listing `guests/*/latest` is the catalog. There is no publish log: each manifest records when and from where it was published.

### 1.1 Bigfoot, the copy of record (role `master`)

- **Root:** `$BF_ROOT`, default `/media/DantePool/bt/BkoStore` **[D1]**. It is at bt's top level, which the weekly backup copies to gmkone, gmktwo and gmkthree without `--del`, so those copies are append-only: a wiped or truncated store can't wipe them.
- **Staging:** `$BF_STAGE = /media/DantePool/bt/.bko-staging`. It is a dot-dir, so it is never backed up, and it is on the same dataset. It holds `incoming/`, `incoming/.partial/`, `xfer.lock` and `test-store/`.
- **Who talks to it:** only Chopin **[D2]**. Cinqcent and later hosts get no key there; they fetch from Chopin.
- **What runs there:** vm-store never runs on Bigfoot. Chopin drives it over ssh, with short shell or python scripts sent on stdin. The only commands are rsync, mv, chmod, sync, cat, find and `zpool status -x`.
- **Deletes:** the first cut never deletes anything from `$BF_ROOT`, and nothing temporary is ever written under it.

### 1.2 Chopin, the full mirror (role `mirror`)

- **Root:** `/bkoscratch/bko-store`. It is on the same XFS as `vms/`, so `cp --reflink` works when the fs supports it ([A1], checked in H1).
- **Writer:** Chopin is the only publisher, and the only host that writes to Bigfoot.
- **Cache:** Chopin's mirror is also Chopin's cache. `ensure` there never fetches; a missing object is reported as damage.
- **Never hard-link `vms/` files into the store.** vm-update rewrites them in place.

### 1.3 Caches (role `cache`): Cinqcent, Copland and Cage (Czerny later)

The test scripts' host table, `testvm/hosts.json` (testvm/HOSTS.md), gives these hosts the `cache` role; vm-store itself goes by each root's `store.json`.

- **Root:** `/bkoscratch/bko-cache`.
- **Limits:**
  - `cap` is the most the objects (plus `.incoming`) may use, counted as `st_blocks*512`.
  - `floor` is the free space the fs must keep after a fetch.
  - Defaults are cap 100G and floor 100G [A2]. Both are set in `store.json`, and `VM_STORE_CAP` / `VM_STORE_FLOOR` override them.
  - Cinqcent's are cap 150G and floor 50G (2026-11-13, when it left the fleet role and its `vms/` became links, but for the guests whose disks back kept overlays).
- **Copies:** manifests and `latest` copied from the source are kept forever. They are tiny, and they serve `--offline`.

### 1.4 Version ids

| Guest | VID |
|---|---|
| Has `image-state` | `g` + the first 16 hex of `image-state.generation` |
| No `image-state` | `i` + the first 16 hex of sha256 of the sorted `"<disk name>\t<sha256>\n"` lines, the same rule as t2's `img:` key (`testvm/test2/t2lib/keys.py`) |

- Publishing an existing VID again is a no-op, and the first bytes win.
- `also_seen` and any cross-host comparison are dropped.

### 1.5 Manifest `guests/<G>/versions/<VID>.json`

```json
{"schema":1, "guest":"debian13", "version":"g0c453f01e2a9b7c4",
 "generation":"<64 hex or null>", "updates":3, "parent":"g9a1e…|null",
 "published":"…Z", "publisher":{"host":"chopin","from":"/home/m/backotter-work/vms/debian13",
   "testvm_commit":"263b8fd","tool":"vm-store/1","copy":"reflink|plain"},
 "note":"",
 "disks":[{"name":"debian13.qcow2","role":"boot","virtual_size":17179869184,"file_size":3543212032,
           "chain":["sha256-aaaa….qcow2"],"snapshots":["fresh-install"]}],
 "files":{"vm-run-options":{"object":"sha256-….file","mode":"0644","size":38},
          "id_ed25519":{"object":"sha256-….file","mode":"0600","size":411}}}
```

- `chain` runs from base to top. M1 writes only one-element chains.
- **Disk names are kept exactly** (for example `freebsd15.qcow2` in `freebsd15-ufs`, `zroot-b.qcow2`), so `vm-run-options`' relative `--disk X` keeps working.
- The manifest is never rewritten after publish.

### 1.6 The `latest` rule (decided at publish, under `.publish.lock`)

- No `latest` yet: set it.
- New `g` over a `g` latest: move only if `new.updates > latest.updates`.
- New `g` over an `i` latest: move if the disk sha256s are equal. This is the first vm-update of a state-less guest. `parent` is the `i` VID.
- New `i` over anything: never moves on its own. This is a rebuild.
- Anything else records the version, leaves `latest` alone, and prints why. `--set-latest` forces the move.
- A state-less guest opted in with `publish --changed --stateless G` (§3.0) follows `vms/G`, as with `--set-latest`.
- `parent` is the VID `latest` named when it moved, or null.

### 1.7 The materialized dir `live/<G>/<VID>/<G>/`

The leaf folder is named after the guest. compare.py (`guest_dir.name`, :237) and BUSY_SCRIPT both rely on that.

- **Disks:** relative symlinks `<disk name> -> ../objects/sha256-X.qcow2`, through `live/<G>/<VID>/objects -> ../../../objects`.
- **Sidecars:** copied with the manifest's mode, owned by the cache owner. Copies, so ssh keys get 0600 and nothing can write through to an object.
- **`image-state`:** synthesized as `{"generation","updates","sha256":{name: object sha}}`.
- **`.vm-store.json`:** `{"guest","version","manifest_sha256","materialized"}`.
- **Building it:** in `live/<G>/.<VID>.tmp.<pid>`, then rename, under `lock`.
- **If it already exists:** its marker must match the manifest sha. If it doesn't, ensure fails with "store inconsistency"; it never rebuilds over a dir that may be in use.
- **Chains (M2):** a delta's backing name is stored as `../objects/sha256-<parent>.qcow2`. QEMU resolves that against the directory of the path it was given, and it reaches the parent in all three places:
  - in `objects/`, because `objects/../objects` is `objects/`;
  - in the live dir, through the `objects` link;
  - in consumers that pass the symlink path without realpath (compare.py:1598, ht1.sh:465).
  The leaf dir holds only the disk names, so the `*.qcow2` globs see no extra files.

### 1.8 Sidecars: a denylist

Every regular file in the guest dir, recursively, is stored, except:
- `*.log`, `build.exit`, `*.png`
- `.vm-update*`, `*.lock`, `image-stat.json`, `image-fingerprint.json`
- `.vm-update-last/`, `rebuild-boot*`
- symlinks
- install media: `*.iso`, `*.iso.*`, `*.img`, `*.img.*`, `*.raw*`, `*.tar*`, `*.tgz`, `*.xz`, `*.bz2`, `*.zst`
- any other non-disk file over 64 MiB

Each skip prints one line. `image-state` is read and checked but not stored; the materialized dir gets the synthesized one. Test-guest secrets are stored 0400 and go to the gmk backups with the rest of the store **[D3]**.

## 2. The tool

### 2.1 Files

- `testvm/vm-store`: the entry point (`#!/usr/bin/env python3`, argparse).
- `testvm/store/vmstore.py`: all the logic.
- `testvm/store/README.md`: the user doc.
- `testvm/store/tests/test_vmstore.py`: unit tests.
- `testvm/store/tests/vm-run-guard.sh`: the vm-run guard test.

The only change to an existing file in M1 is the vm-run guard (§2.9), plus the same guard in `vm-mount` and `qcow2-punch.py`. vm-update is not changed.

### 2.2 Configuration

The environment overrides `store.json`, which overrides the defaults.

| Variable | Default |
|---|---|
| `VM_STORE_ROOT` | `/bkoscratch/bko-store` if it exists, else `/bkoscratch/bko-cache` |
| `VM_STORE_SOURCES` | `chopin=m@chopin.local:/bkoscratch/bko-store` (in order; a bare path is a local source, used by tests). Bigfoot is not a default source **[D2]**; on Chopin, `bigfoot=m@bigfoot.local:$BF_ROOT` may be added for a test cache root (H6) |
| `VM_STORE_BIGFOOT` / `VM_STORE_BIGFOOT_STAGE` | `$BF_ROOT` / `$BF_STAGE` |
| `VM_STORE_BWLIMIT_BIGFOOT` | `40000` (KiB/s) |
| `VM_STORE_WINDOW` | `00:30-07:30` (push only; `--now` overrides) |
| `VM_STORE_RSYNC_TIMEOUT` | `300` (seconds: `rsync --timeout` of every rsync to or from Bigfoot, §2.10) |
| `VM_STORE_BF_POOL` | the first path component under `/media/` of `$BF_STAGE` (`DantePool`), else `DantePool` |
| `VM_STORE_BF_RESET_WINDOW` | `6h` (how far back the gate looks for USB resets; `0` skips that check) |
| `VM_STORE_BF_GATE` | `1`; `0` skips the health gate, with a warning (§2.10) |
| `VM_STORE_COPY` | `auto` (try `--reflink=always`, else plain); `plain` forces a copy |
| `VM_STORE_FLOOR_REFLINK` | `0`; `1` lets publish count a reflinked copy as no new space against the floor (§2.4) |
| `VM_STORE_SCAN` | `/mnt/bko-ram /dev/shm /bkoscratch` |
| `VM_STORE_MIN_AGE` | `24h` |
| `BKO_WORK` | `~/backotter-work` (`vms/` = `$BKO_WORK/vms`) |

**Remote and progress rules:**
- Every ssh and rsync-over-ssh uses `-o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=15`, so nothing waits on a password.
- Every rsync passes `--no-whole-file` (local paths would otherwise skip partials) and `--fsync`.
- Every rsync to or from Bigfoot also passes `--timeout=$VM_STORE_RSYNC_TIMEOUT`, and every ssh to it runs under a local timeout (§2.10).
- All progress output goes to stderr. stdout carries only the result.

### 2.3 CLI and exit codes

```
vm-store init ROOT --role mirror|cache [--cap SIZE --floor SIZE]
vm-store init --bigfoot                       # from Chopin: mkdir $BF_ROOT/{objects,guests}, store.json role master
vm-store publish GUEST... | --changed [--from DIR] [--expect-generation HEX] [--set-latest] [--note T] [--push] [--dry-run]
vm-store push [--now] [--dry-run]            # Chopin -> Bigfoot: whatever Bigfoot lacks
vm-store ensure GUEST[@VID|@latest] [--offline] [--json]   # prints the materialized dir
vm-store fetch GUEST[@VID]...                 # ensure without output (prefetch)
vm-store gc [--dry-run] [--json]              # caches only in M1
vm-store list [GUEST] [--source NAME] [--json]
vm-store status [--json]                      # role, use/cap/free/floor, in-use objects and why, unpushed (Chopin)
vm-store verify [--where local|bigfoot] [GUEST...] [--json]
vm-store bigfoot-health [--pool-only] [--json]  # Chopin: exit 0 healthy, 1 not (§2.10)
```

| Exit | Meaning |
|---|---|
| 0 | ok |
| 1 | failure |
| 2 | usage |
| 3 | busy (try later) |
| 4 | no room (cache full of in-use objects, or below the floor) |

### 2.4 `publish` (Chopin only; it refuses unless the root's role is `mirror`)

It works on one guest at a time. `--changed` means every `vms/G` with an `image-state` whose VID has no manifest, smallest first.

1. **Fleet lock.** Take `vms/.vm-update.fleet.lock` `LOCK_EX|LOCK_NB`, the same file vm-update uses. If it is held, exit 3 ("a vm-update round or update is running").
   - This excludes real updates and `--finish` on every host.
   - It does not take the guest lock, so a GuestTest2 `--try` or `--probe` keeps working. They only read the base.
   - A vm-update starting in this short window fails at once and harmlessly with "the fleet lock is held"; re-run it. Run the weekly publish in quiet hours.
2. **Refusals:**
   - `.vm-update.pending`, `.vm-update.*.qcow2` or `*.merging*` in the dir.
   - A disk that `qemu-img info -U --output=json` shows with a backing file, `corrupt`, or a format other than qcow2.
   - Disks are found by vm-update's `Guest` rule: the single boot `*.qcow2`, skipping `vm-run.*` and `.vm-update.*`, plus the `--disk` names in `vm-run-options`.
3. **Writer fence**, per disk:
   1. Open it O_RDONLY.
   2. Take an OFD `F_RDLCK` on byte 201, which blocks any new QEMU writer.
   3. Probe `F_OFD_GETLK F_WRLCK` on byte 101. If something holds it (a running writable boot, a vm-mount without `--ro`), release everything and exit 3.
   - Readers (`--snapshot`, `--ram`, compare, ht1) are not affected.
4. **Copy**, while holding the fences: `stat` (size, mtime_ns, ino), then copy into `objects/.incoming/tmp.<rand>` with `cp --reflink=always`, falling back to `cp --sparse=always`. Record which one ran in the manifest.
5. **Release** the fences and the fleet lock. With reflink they are held for seconds; with a plain copy, about a minute for 10 GiB.
6. **Re-stat the source.** Size, mtime_ns and ino must be unchanged, or it discards the copy and exits 1.
7. **Hash the copy** (`nice -n19 sha256sum`, no second read of the source).
   - If `image-state` exists, the sha must equal `image-state.sha256[name]`, otherwise "image changed since vm-update recorded it", discard and exit 1.
   - With `--expect-generation`, the generation must match too.
8. **Sidecars:** hash and stage each one (§1.8).
9. **Under `.publish.lock`:**
   1. If `versions/<VID>.json.sha256` exists, drop every staged file, print "already published" and stop (exit 0), applying §1.6 again only to finish a `latest`. This comes before any object is installed: an `i` VID names only the disks, so a re-publish after a sidecar edit would otherwise leave objects no manifest names.
   2. Each staged file is fsynced, gets `chmod` 0444 (`.qcow2`) or 0400 (`.file`), then `rename` to `objects/sha256-X.*`. If the name already exists, the copy is dropped (dedupe).
   3. `fsync` of `objects/`.
   4. Then write the manifest, then `.sha256` (each tmp+rename+fsync), then apply §1.6 and write `latest`.
   - The order is always objects, then manifest, then latest.
10. **`--push`** runs §2.5 afterwards. A push failure does not undo the publish. `status` then shows the version as unpushed.

**Free-space floor.** Publish refuses (exit 4) if Chopin's `/bkoscratch` free space minus the bytes the copies write now would fall below the floor (default 200G on the mirror). By default those bytes are each disk's allocated size, reflinked or not. With `VM_STORE_FLOOR_REFLINK=1` (opt-in) a reflinked copy (`VM_STORE_COPY=auto`, the disks on the store's file system, and a probe reflinking there) counts as none, so it passes even below the floor, with a warning; vm-update's next merge un-shares what it rewrites, unchecked. A reflink that fails at copy time is checked again at its allocated size before the plain copy.

### 2.5 `push`, Chopin to Bigfoot

It runs under `flock /bkoscratch/bko-store/.push.lock` and inside `VM_STORE_WINDOW`, unless `--now`. Then, before it lists or sends anything, it must pass the health gate (§2.10); `--now` never skips that.

1. **List Bigfoot.** `ssh bigfoot find $BF_ROOT/objects $BF_ROOT/guests -type f -printf '%P %s\n'`.
2. **Send missing objects.** For objects that are missing, run one rsync with `--files-from`. An object Bigfoot has with another size is never sent again or replaced: push reports it and exits 1, and `verify --where bigfoot` lists it:
   ```
   rsync -t --chmod=Fa-w --fsync --no-whole-file --partial-dir=.partial \
     --bwlimit=$VM_STORE_BWLIMIT_BIGFOOT --compress --compress-choice=zstd --compress-level=1 \
     --rsync-path="flock -w $((VM_STORE_RSYNC_TIMEOUT - 30)) -E 99 $BF_STAGE/xfer.lock nice -n19 rsync" \
     --timeout=$VM_STORE_RSYNC_TIMEOUT \
     -e "ssh -o BatchMode=yes -o ConnectTimeout=5" \
     --files-from=LIST /bkoscratch/bko-store/objects/ m@bigfoot.local:$BF_STAGE/incoming/
   ```
   - Staging is keyed by object name, so a re-run resumes from `incoming/.partial`.
   - rsync's whole-file checksum checks the transfer against Chopin's object, which was already sha-verified. Bigfoot does not re-hash: that read would mostly hit ARC anyway.
3. **Accept on Bigfoot.** An ssh script on stdin:
   - For each listed name: `mv -n incoming/X $BF_ROOT/objects/X`. This is a rename on the same dataset. If X is already there with another size, that is reported (push exits 1), never replaced.
   - Then `sync -f $BF_ROOT/objects`.
   - Then delete anything in `incoming/` older than 7 days.
4. **Manifests.** Only complete manifests are sent (the `.json` matching its `.sha256`: a lone `.json` is an unfinished publish), and always as a pair, into `incoming/meta/` first and then `mv -n`. Bigfoot takes a pair only when the sha256 of the `.json` equals its `.sha256` and, to complete it, every object it names is there. A `.json` or `.sha256` there with other bytes is reported, not replaced.
5. **`latest` files.** Rsync to `incoming/latest/<G>`, then `mv -f` over `$BF_ROOT/guests/<G>/latest`, only when the manifest it names is whole there (its `.json`'s sha256 equals both its `.sha256` and the `latest` line).
6. **Ordering invariant on Bigfoot:** objects, then manifests, then latest. A reader that sees a `latest` there can always fetch all of its objects.

Bigfoot being down never blocks publishing or fetching from Chopin. It only delays push.

### 2.6 `ensure GUEST[@VID]`

1. **Resolve.**
   - `@latest` (the default): `cat guests/G/latest` from the first source that answers within the timeout. With `--offline`, or when no source answers, use the cached `latest` and print a warning.
   - **Manifest:** use the cached `guests/G/versions/VID.json` if it is there and its sha matches its `.sha256`. Otherwise fetch both from the first source that has them; Chopin is tried first, so it can lag Bigfoot.
   - **Checks:** the sha must match, and a source whose `.sha256` differs from the cached one is an error ("store divergence"). Once cached, the copy is authoritative.
2. **Touch stamps.** Touch `used/X` for every object of the version (chain objects and files) *before* anything else. This protects the whole version from a concurrent gc (§2.7 rule c).
3. **Fast path, without the lock:** if every object exists and `live/G/VID/G/.vm-store.json` matches, print the dir and exit 0.
4. **Slow path:** take `lock` EX (blocking; one fetch at a time per host suits 1 GbE), then re-check. For each missing object:
   1. **Room.** `need_room(file_size)`: if use + incoming > cap, or free − incoming < floor, run gc inline with protect = this version. If there is still no room, exit 4 ("N GiB in use: …").
   2. **Fetch:** `rsync -t --fsync --no-whole-file --sparse --partial-dir=.partial [--bwlimit] [--compress…] SRC:objects/X objects/.incoming/`, under `nice -n19`.
      - For a Bigfoot source, add the `--bwlimit`, zstd and the `flock xfer.lock` `--rsync-path` from §2.5.
      - Chopin needs none of that.
   3. **Verify.** sha256 of `.incoming/X` must equal X. On a mismatch, delete it and try the next source. The fetch fails if no source gives good bytes, and names the object with "run `vm-store verify` on chopin".
   4. **Install.** `chmod` by kind, `rename` into `objects/`, then `fsync` the dir. **An object is visible only after its hash is verified.**
5. **Materialize** under `lock`, if absent (§1.7). Every file and directory is fsynced before the rename makes it visible.
6. **Cache `latest`** only now, once the whole version is here: a failed fetch leaves the cached `latest` naming the last version that is, so `--offline` keeps working.
7. **Release** `lock`, then print the dir (or JSON `{"dir","version","disks","objects"}`).

On the mirror role, step 4 never fetches: a missing object is an error.

### 2.7 `gc` (role `cache` only in M1)

It runs under `lock` EX, both from ensure and by hand. An object is **in use**, and kept, if any of these hold:

- **(a) A QEMU process has it open.** An OFD `F_WRLCK` GETLK on bytes 100-299 of the object conflicts with a holder.
- **(b) A qcow2 file references it.** gc walks `VM_STORE_SCAN` (skipping the store roots' `objects/`) and, for every `*.qcow2` it finds, reads the header and follows the chain:
  - magic `QFI\xfb`;
  - backing offset is a u64 at byte 8, length a u32 at byte 16;
  - a relative name resolves against the file's dir, then **realpath**s to an object.
  This covers idle kept overlays (ht1 R dirs, compare work, backshot2, t2), which can last for days.
- **(c) Its `used/` stamp is younger than `VM_STORE_MIN_AGE`** (24h). This covers the gap between ensure and QEMU opening the file, and `--snapshot` temp files that aren't named `*.qcow2`.
- **(d) Blind scan.** If the scan met a directory or a `*.qcow2` (or a link of its backing chain) it can't read (root's mktemp dirs, a root vm-run's 0600 `--ram` overlay), or rule (f)'s walk of `$BKO_WORK/vms/` met a directory it can't read (a sudo `compare.py`'s 0700 one, which may hold a link), the age in (c) rises to 7 days for this run, and those paths are reported.
- **(e) The rest of its version.** Every object of a version that has any in-use object is in use too, along with all chain ancestors. This uses the cached manifests.
- **(f) What `$BKO_WORK/vms/` links to (§3.0).** Every object of a version that a `vms/G` link resolves into (`live/G/VID`, dangling or not), and each object a link inside a real `vms/G` resolves to (that one before rule e, as an overlay's). The version rule applies after rule e, so a sidecar object shared with older versions keeps none of them. gc_live never removes a `live/G/VID` a link points into.

**Eviction:**
1. Delete the objects not in use, oldest `used/` stamp first, until use ≤ cap and free ≥ floor. The stamp mtime is read again just before each delete.
2. Delete each `live/G/VID` whose objects are gone, and its stamps.
3. Delete `.incoming` entries older than 7 days.

`--dry-run` prints the plan, with a reason for each kept object (`qemu lock`, `overlay /mnt/bko-ram/…/src-0.qcow2`, `used 3h ago`). Leases and `release` are dropped.

### 2.8 `verify`

- **`--where local`:**
  - re-hash every object (`nice -n19`) and compare it with its name;
  - each manifest matches its `.sha256` and schema 1;
  - every object it names exists, at its `file_size`. On a cache, which keeps manifests after gc evicts their objects, an absent object is counted as not cached, not as a problem;
  - `qemu-img info -U --backing-chain` of each disk object matches `chain`;
  - every `latest` names an existing manifest whose sha it gives.
- **`--where bigfoot`:** no re-hash, so no 166+ GiB of HDD reads on the user's daily machine.
  - Remote `find -printf` names and sizes are compared with Chopin's.
  - Remote `cat` of every `*.json.sha256` and `latest` is compared with Chopin's, and Bigfoot hashes each `*.json` (they are small): it must equal Chopin's and its own `.sha256`.
  - Bit rot on Bigfoot is ZFS raidz2's job (`zpool scrub`). verify prints `zpool status -x $VM_STORE_BF_POOL`, and it reads nothing from Bigfoot unless the pool is ONLINE (§2.10).
- Output is JSON plus one text line per problem.

### 2.9 The write guard: vm-run, vm-mount, qcow2-punch.py

It is root-proof, because it does not test `-w`. A path is a store object if its realpath lies in (or below) some `ROOT/objects/` whose `ROOT` holds `store.json`. Other files under a store root, such as H2's synthetic guest in the test root, are not objects:

```bash
in_store() { local d; d=$(dirname "$(realpath -- "$1")")
  while [[ $d != / ]]; do [[ $(basename "$d") == objects && -f $(dirname "$d")/store.json ]] && return 0; d=$(dirname "$d"); done; return 1; }
```

- **`vm-run --ram --keep`** on a store object prints only how to delete the kept overlay, never `qemu-img commit` into the object.

- **`vm-run`:** in `drive_opts()` (after :160), and for each `--disk`, when neither `--ram` nor `--snapshot` is set: `in_store "$image" && die "$image is a vm-store object: boot it with --ram or --snapshot"`.
- **`vm-mount`:** the same check without `--ro`.
- **`qcow2-punch.py`:** the same check in python, before `os.open(O_WRONLY)` (:144).
- Nothing in today's `vms/` is affected.
- The network-filesystem check is dropped. No network mount exists, store sources are rsync URLs only, and vm-store has no boot command.

### 2.10 Bigfoot's health gate and timeouts

Added after 2026-11-13. That night 4 of DantePool's 6 raidz2 disks dropped behind a flaky USB hub, and the pool suspended (`failmode=wait`). A push without `--timeout` then hung in rsync for about 8 h, and a retry pushed onto the still-flaky pool.

- **The probe** (`bigfoot_health`, also `vm-store bigfoot-health`) is one ssh round trip under a local 45 s timeout. No answer in time counts as unhealthy; the output read so far still counts, so a SUSPENDED pool is named even when `zpool status` hangs after it. A `sh` script there runs each step under `timeout -k 2 5` (four such steps and ssh's connect fit in the 45 s) and keeps going after a step fails:
  - `zpool list -H -o health $VM_STORE_BF_POOL`;
  - `zpool status` (for a scrub or resilver in progress);
  - `sudo -n journalctl _TRANSPORT=kernel --since=-$VM_STORE_BF_RESET_WINDOW`, filtered to USB reset, disconnect, descriptor-read and `error -71` lines. Not `-k`, which implies `-b`: resets before a reboot inside the window count, as long as Bigfoot's journal is persistent;
  - after a failed rsync only, and only on an ONLINE pool: `flock -n` on `xfer.lock`, to say whether another transfer still holds it.

  If sudo can't run journalctl without a password, that check is skipped with a warning; it does not fail. The probe's ssh runs in a process group of its own, which is killed on the timeout and on any signal to vm-store.
- **Writers** (push, and later the results and attic syncs) need all three: health `ONLINE`, no scrub or resilver, and no USB reset line in the window. Otherwise push exits 1 (`push: Bigfoot is not healthy: …; not pushing`) before any rsync. `--dry-run` reports the verdict without failing; with the pool not ONLINE (or no answer) it doesn't list Bigfoot either. `--now` skips only the window.
- **Readers** need only an ONLINE pool, since a suspended pool hangs anything that reads it. These are `verify --where bigfoot`, `status` on the mirror, `list --source bigfoot`, `init --bigfoot`, and ensure or fetch from a Bigfoot source (named `bigfoot`, or any source on `VM_STORE_BIGFOOT_HOST`'s host). An unhealthy Bigfoot source counts as down for the rest of the run, so ensure goes on to the next source.
- **Timeouts:** every rsync to or from Bigfoot passes `--timeout=$VM_STORE_RSYNC_TIMEOUT` (300 s). Its ssh calls run under 120 s (listing, accept) or 45 s (probe, `zpool status -x`). When a Bigfoot rsync exits non-zero (30 = I/O timeout; 12, 35 and 255 = a dropped link), the probe runs once and its verdict goes into the message. If the pool is not healthy, push stops there, without sending the rest or running the accept step.
- **xfer.lock:** the client's `--timeout` also counts the silent wait while the remote `flock` queues for `xfer.lock`, so a plain `flock -w 3600` would die at 300 s with exit 30, looking like a network fault. Each try's `flock` therefore waits `VM_STORE_RSYNC_TIMEOUT - 30` s (270 s) and then exits 99 (`-E 99`, no rsync status). vm-store tries again on 99, up to 3600 s in all, which keeps the one-stream queue. rsync passes the remote shell's status on only when it sees it in time; when a lock wait ends as 12 or 30 instead, the probe after the failure says the lock is still held.
- **Escape hatch:** `VM_STORE_BF_GATE=0` skips the gate, with a loud warning. The probe after a failed rsync still reports.
- **Not built yet** (see the postmortem): a watchdog during long pushes, a "bigfoot unhealthy" marker for later jobs, routing the results and attic syncs through the flock, bwlimit, window and gate (they can call `vm-store bigfoot-health`), and re-hashing recent objects after a suspension.

## 3. Using the store (consumer wiring): M3, when the first M900 is racked

Chopin and Cinqcent keep their full `vms/` in the first cut, so wiring consumers there now would only add version mismatches. This is the wiring for the M900s.

### 3.0 Interim, before the pins: `link`

The M900s (cage, copland) run the test scripts unchanged on a `vms/` of links: `$BKO_WORK/vms/G -> ROOT/live/G/VID/G` on the cache root. Until the pins below are built, `vm-store link` keeps those links current:

- **`link GUEST[@VID]... | --all [--offline] [--dry-run]`**, on a cache root only. For each guest: ensure the version (default latest; fetching it, or `--offline`), then point `vms/G` at its leaf by one `rename` of a temp link, so `vms/G` is the old link or the new one, never absent. `--all` takes every `vms/G` that already links into this store's `live/G/` (hand-made, or dangling); it adds none.
- **Refusals.** A real `vms/G` folder, or a link that points anywhere but this store's `live/G/`, is never replaced (exit 1, before any fetch). While a VM of G runs, link refuses (exit 3, after the fetch): a QEMU program holds one of the current version's disk objects (rule a's lock test), or a `compare.py` / `compare-freebsd.sh` of G runs (as vm-update's busy check). The re-point runs under `lock`, so gc sees the old link or the new one.
- **gc** keeps whatever the links use (rule f, §2.7): a linked guest is never evicted, and the version a re-point leaves behind ages out by its stamps. A cache's cap must hold every linked guest plus one new version.
- **After a publish** run `link --all` on each cache host. Nothing schedules it yet.
- **State-less guests** reach the caches once published: by name, or weekly with `publish --changed --stateless G,...` (opt-in, §1.6).
- When the pins land, `link` stays for the hosts whose test scripts read `vms/` directly.

1. **Resolve once, prefetch outside the slot.**
   - The m-side driver (the caller of cmp-run.sh, ht1-queue.sh, mark/stic) runs `vm-store fetch G...` **before** `cmp-run.sh`. A fetch never holds a slot or a RAM reservation.
   - It writes `VM_STORE_PIN="G=VID,RESCUER=VID"` into the job's environment or run dir.
   - Inside a job, ensure runs with `--offline` plus the pin, and fails fast if an object is missing.
   - Version is per guest. There is no global `VM_STORE_VERSION`.
2. **Local shortcut.** When `$BKO_WORK/vms/G` exists and its `image-state` gives the pinned VID, return that dir. On hosts with a full `vms/` this changes nothing.
3. **Root.** When EUID is 0, vm-store re-execs as the owner of the cache root (`setpriv --reuid/--regid` from `stat`), and only with `--offline` (no fetch). That keeps cache files owned by m. The caller passes the pin through `sudo env VM_STORE_PIN=…`.
4. **Hook points:**
   - `vm-lib.sh vm_boot` (:48-56): `dir` comes from a `vm_store_dir` helper when `VM_STORE_PIN` names the guest.
   - `compare.py`: a `guest_dir()` used at :1574 and :1746. `boot_only` reads the VID from `results.json`.
   - `ht1.sh` (:172, :181-190), `ht1-bsd.sh` (:204-228), `ht1-offline.sh`.
5. **The ten checks that bypass vm_boot**, each switched to the helper:
   - dragonfly6
   - `haiku6/guest-lib.sh:3`
   - `sculpt2610/check.sh:33`
   - `ubuntu26-zfsmirror/check.sh:63`
   - `omarchy/om-boot.sh:8`
   - `haiku/roundtrip.sh:29`
   - `mx25`, `mint22`, `pop24`, `zorin18` `check.sh:32-34`
6. **Identity.** With a pin set, t2 facts (`facts_host.py`) and `rows_stic.py` key on `.vm-store.json` and the synthesized `image-state` of the dir actually used, not on `$WORK/vms/G`.
7. **HoltTest rescuers.**
   - Publish `/bkoscratch/holttest1d/rescuer/{rescuer,rescuer-uefi,*-newduid,*-reguid,*-newfsid}.qcow2` as store guests (`publish --from DIR --as holt-rescuer`, …), and likewise `vms-bko-build/*-base.qcow2`.
   - With a pin, ht1 **fails** instead of falling back to `vms/debian13` (ht1.sh:171-172).
8. **The shared slim path is not in the store.** The M900s get `server/slim-*` by the existing tar procedure (memory `shared-slim-path`).

## 4. Setup and seeding

**S0, one time, approved by the user.** This is the only sudo:
- On Bigfoot: `sudo install -d -o m -g video -m 2775 /media/DantePool/bt/.bko-staging /media/DantePool/bt/BkoStore` (both on the dataset DantePool/bt, so push's renames are atomic) **[D1]**.
- Then, from Chopin:
  - `vm-store init --bigfoot`
  - `vm-store init /bkoscratch/bko-store --role mirror`
  - `ssh cinqcent … vm-store init /bkoscratch/bko-cache --role cache`

**S1, the seed.**
- **What:** the 40 guests that have `image-state`. `freebsd15-builder` has none and is a pet; the guests without `image-state` are in active development (chimera*, ubuntu26-luks, endeavouros, manjaro). Those go in later, by name, once stable.
- **How:** two commands on Chopin:
  ```
  WEAR_TAG=store:seed cmp-run.sh --reserve 1 1 vm-store publish --changed
  WEAR_TAG=store:push cmp-run.sh --reserve 1 1 vm-store push
  ```
  The push runs in the window.
- **Busy guests** exit 3 and are picked up by the next `--changed`.
- **Volume:** about 166 GiB allocated. At 40 MB/s that is about 70 min of transfer, so one night.
- **Chopin disk:** near zero with reflink, else +166 GiB, out of 1.1T free.

**S2, check.** `verify --where local` on Chopin, `verify --where bigfoot`, and `list` on Cinqcent from the chopin source.

**After the seed:**
- Run `publish --changed --push` weekly **[D5]**, by hand; nothing schedules it. Not after every vm-update round, and not per merge.
- For new guests: `vm-store publish G` on Chopin. A guest built on Cinqcent goes in through `--from-host` in M2; until then it is copied to Chopin by hand, as today.

## 5. Safety

- **Running tests are undisturbed.**
  - `vms/` is only read.
  - The fleet lock and the byte-201 fence are each held for one copy (seconds with reflink).
  - No guest lock is taken.
  - Nothing touches `/mnt/bko-ram/.reserve*` or the cmp-run slots.
  - No pkill; vm-store kills only its own children.
  - vm-update is not changed.
- **No guest runs from Bigfoot or over the network.** vm-store has no boot command and sources are rsync URLs. Bigfoot gets one inbound or outbound stream at a time (`xfer.lock`), 40000 KiB/s of compressed wire bytes (`--bwlimit`; the logical write rate can be several times that), CPU nice 19, and pushes only in the window and only past the health gate (§2.10).
- **Integrity:**
  - An object is visible only after verification: a sha256 check on hosts, and on Bigfoot rsync's whole-file checksum of an already-verified object.
  - Every rsync uses `--fsync`, and directories are fsynced after renames.
  - The order is always objects, then manifest, then latest.
  - Manifests carry a sha256 and are checked before they are trusted.
  - Store objects can't be written in place, even by root (§2.9).
- **Chopin down:** caches keep working for whatever they hold (`ensure` falls back to the cached `latest`, as `--offline` does). Publishing stops. No other host fetches from Bigfoot **[D2]**.
- **Growth:**
  - The seed is about 166 GiB on Bigfoot, and the same again on each gmk host, kept there for good (append-only, **[D1]**).
  - Until M2 deltas, every full-fleet republish adds about 166 GiB on Bigfoot, on Chopin's mirror and on each gmk host.
  - With deltas, about 0.2-1.5 GiB per guest per round [A3].
  - Until M2, publish deliberately: changed guests weekly **[D5]**, so at most one new version per guest per week. Chopin's floor stops the mirror from filling the disk.

## 6. Tests

### 6.1 Unit tests (`cd testvm/store && python3 -B -m unittest tests.test_vmstore`)

- They use temp-dir roots (mirror, master, two caches) with local-path sources.
- A fake `rsync` on PATH, when needed, counts calls and can be made to stop partway (SIGTERM to its process group).
- qemu-img cases are skipped on the Mac.

Cases:
1. Hashing, naming, dedupe, and modes 0444/0400. The VID rules (`g`, and `i` equal to t2 `keys.py`). A second publish is a no-op.
2. Publish refusals:
   - fleet lock held (exit 3);
   - a `.vm-update.pending` or `.vm-update.*.qcow2` file;
   - an `image-state` sha mismatch;
   - `--expect-generation` mismatch;
   - a backing file;
   - a writer on byte 101 (a child process holding an OFD read lock there; exit 3);
   - a source changed during the copy.
   - A reader holding 201 does **not** block publish.
3. The `latest` rule table (§1.6), including g over i with equal shas, and `--set-latest`.
4. Ordering: a crash injected after the objects, and after the manifest, leaves `latest` unchanged and verify clean.
5. Ensure:
   - the leaf dir is named after the guest, and `GuestConfig(dir).base` exists (import compare.py);
   - `freebsd15.qcow2` inside `freebsd15-ufs`;
   - `--disk` resolves;
   - sidecar modes;
   - the synthesized `image-state`;
   - an existing dir with a wrong marker fails.
6. Fetch:
   - nothing appears in `objects/` before its sha passes;
   - an interrupted fetch resumes from `.partial` without a second full transfer;
   - a corrupt source falls back to the next source;
   - an unreachable first source;
   - `--offline` uses the cached `latest`;
   - a manifest sha mismatch fails.
7. gc:
   - LRU order;
   - kept: a fresh stamp, a QEMU-style lock, an overlay found by scan (both relative and absolute backing, and a backing through a `live/` symlink, realpathed);
   - an unreadable scan dir raises the age to 7 days;
   - all objects of a version are protected together;
   - the floor counts;
   - refuses on mirror and master roles;
   - exit 4 when everything is in use.
8. Concurrency: two ensures of one object call rsync once. An ensure needing gc never evicts its own version.
9. `qcow2_backing()` on hand-built v2 and v3 headers.
10. Push command lines: `--rsync-path` flock, bwlimit, zstd, `--no-whole-file`, `--fsync`; nothing is ever written under `$BF_ROOT` except by `mv`.
    - The health gate (§2.10), with a fake `zpool` and `sudo` on PATH: probe parsing (ONLINE, SUSPENDED, DEGRADED, scrub, resilver, USB resets in the window, sudo unavailable is a warning, no answer in time is unhealthy); push refuses when unhealthy, `--now` doesn't bypass it, `--dry-run` doesn't fail and `VM_STORE_BF_GATE=0` bypasses it; readers gate only on the pool; `--timeout` in every Bigfoot rsync; a failed rsync's message carries the probe's verdict; `bigfoot-health`'s exit codes; a probe that times out keeps what it read, and a signal kills its ssh; the `xfer.lock` wait fits in `--timeout` and is retried on 99; a busy lock is named after a failed rsync.
11. `tests/vm-run-guard.sh` with fake `qemu-img` and `qemu-system-x86_64` on PATH:
    - a store object without `--ram`/`--snapshot` is refused, including when run as root (`in_store` doesn't use `-w`);
    - `--ram` is allowed;
    - a `vms/` image is unchanged.

### 6.2 Host tests

Common to all steps:
- **Code location:** run from `~/backotter-work/image-store-wt/` on both hosts, an rsync of the worktree. Never sync-hosts.sh before merge.
- **When:** run when no vm-update round holds the fleet lock.
- **Test roots:** `/bkoscratch/bko-store-test` (Chopin), `/bkoscratch/bko-cache-test` (Cinqcent, cap 3G), and `$BF_STAGE/test-store` (never backed up). `VM_STORE_SOURCES` points at those.
- **Guests:** `alpine324`, plus a synthetic 3-disk guest made with `qemu-img create` inside the test root.

| Step | What |
|---|---|
| H1 | On Chopin, `publish alpine324` into the test root. Check: `vms/alpine324` size, mtime, ctime and sha unchanged; the fleet lock and fence held for seconds; `cp --reflink=always` worked (A1). If reflink changes the source's ctime (t2's imgsum cache keys on ctime), set `VM_STORE_COPY=plain` |
| H2 | Check the fence for real: with a `vm-run --ram` reader on alpine324, publish works. With a writable `vm-run` of a *synthetic* test-root guest, publish exits 3. While publish holds the fence on the synthetic guest, a writable `vm-run` of it fails to start |
| H3 | `push --now` to the Bigfoot test root. Check sizes; ~40 MB/s on the wire, and the write rate measured on Bigfoot's side (`zpool iostat`), which `--bwlimit` does not cap; `flock`, `nice` and `rsync` visible in Bigfoot's `ps`; nothing left in `incoming/` |
| H4 | On Cinqcent, `ensure alpine324` from the Chopin test root, then under `WEAR_TAG=store:h4 cmp-run.sh --reserve 2 2` boot it once on a free port (checked with `ss -ltn`, outside 2400 and 2600-2699) with `vm_boot … --ram`, run `vm_ssh true`, halt. `qemu-img info` of the overlay names the object, and the object's sha is unchanged |
| H5 | Resume: fetch a 1-2 GiB synthetic disk, SIGTERM rsync's process group partway (our pid only, never pkill), re-run; it resumes and verifies |
| H6 | Bigfoot fallback, on Chopin with a second test cache root: point the chopin source at a missing path, and the fetch comes from the Bigfoot test root at the bwlimit. (Chopin only: by D2 no other host talks to Bigfoot) |
| H7 | gc with cap 1G: the H4 VM running again on object A, plus an idle overlay on B in `/mnt/bko-ram`, and the stamps backdated. A and B are kept with their reasons; the others are evicted |
| H8 | `verify --where local` and `--where bigfoot`, `list` and `status` on the test roots. Then delete only our test roots |

## 7. Milestones

| | Contents |
|---|---|
| **M1** (about a day) | `vmstore.py` and `vm-store`: init, publish, push, ensure, fetch, gc, list, status, verify; the write guard in vm-run, vm-mount and qcow2-punch; the unit tests |
| M2 | H1-H8, then S0-S2 (each with user approval). Deltas: vm-update keeps a copy of the overlay it shipped (opt-in env) on Chopin, and publish stores it as a chain part when the store's latest generation equals the overlay's pre-merge generation; `qemu-img convert` flattens past 4 links. Mirror prune (`gc --keep 2` on the mirror; Bigfoot keeps everything). `verify --repair` on Chopin (re-pull from Bigfoot). `publish --from-host H` (the remote runs the same fence-and-copy step into its own `.incoming`, and Chopin pulls and verifies it) |
| M3 | Consumer wiring and rescuer publishing (§3), when the first M900 arrives |
| Later | Install media as `.media` objects; replacing `swap-guests.sh`; M900s in vm-update's `ALL_HOSTS`; retiring full `vms/` copies; read-only restricted keys for fetchers |

Out of scope:
- `vms/freebsd15-builder` and `vms-bko-build/*-rust.qcow2` (writable pets)
- `/bkoscratch/imgbuild` and the rescue `.img` files (`img-xcopy.sh` stays)
- `img-pin`
- any change to sync-hosts.sh

## 8. Assumptions and decisions

**Assumptions:**

| | |
|---|---|
| A1 | Reflink on Chopin's XFS, and whether it changes the source's ctime (H1) |
| A2 | Cache cap and floor of 100G each, to be re-sized per M900 when its disk is known |
| A3 | Delta sizes of 0.2-1.5 GiB per guest per round, to be measured in M2 |

**Decisions:**

| | |
|---|---|
| D1 | **Decided 2026-11-12: root `/media/DantePool/bt/BkoStore`**, staging `/media/DantePool/bt/.bko-staging` (a dot-dir, not backed up; same dataset). Why: `bt/Software/` is backed up with `--del`, so a wiped store would wipe the gmk copies; bt's top level is copied without `--del`, so they stay append-only (a later prune frees no gmk space) |
| D2 | **Decided 2026-11-12: only Chopin talks to Bigfoot.** No keys for Cinqcent or later hosts; with Chopin down, caches use what they hold (`--offline`). Why: one host with access to the user's personal server |
| D3 | **Decided 2026-11-12: test-guest secrets go in the store (0400)**, and so into the gmk backups. Why: the tests need them to reach the guests, and they are test-only keys |
| D5 | **Decided 2026-11-12: publish changed guests weekly**, by hand (`publish --changed --push`), not after every round; no scheduler. Why: until M2 deltas each version is a permanent full copy on Bigfoot and each gmk host |

## 9. Critique items rejected or changed, and why

- **"D1 is settled, don't ask again" (fit critique 25):** rejected. The `--del` on `Software/` (correctness critique 1) was new and changed the trade-off, so D1 was asked again; the user chose `bt/BkoStore` (§8).
- **ZFS snapshots of DantePool/bt (correctness critique 1, option B):** not adopted. It needs sudo and ongoing admin on the user's personal server, and the append-only gmk copies (D1) cover the case.
- **"Keep only the QEMU lock probe plus a 24h stamp" (fit critique 21):** half taken. Leases, `release` and the SH/EX lock split are dropped. The header scan stays, because idle overlays last for days (correctness critique 9: `/bkoscratch/holttest1d/raid2/…` from 11-07). It is widened to all of `/bkoscratch`, `/mnt/bko-ram` and `/dev/shm`. Unreadable dirs raise the age to 7 days instead of protecting every object.
- **"Require ctime unchanged on state-less guests" (correctness critique 5):** replaced by the byte-201 fence, which stops writers outright. A reflink copy may itself change the source's ctime; H1 checks that.
- **Fleet lock plus guest lock (fit critique 4):** the fleet lock is taken, but the guest lock is not. Holding it would make GuestTest2's `--try` and `--probe` jobs, which take it `LOCK_NB`, fail for no gain.
- **Trusting rsync's checksum instead of a sha (fit critique 11):** taken for Bigfoot only. Caches still sha-check every fetched object, because a corrupt Chopin object would pass rsync's check.
- **Moving deltas to M2 (fit critique 6):** taken (M2), but not in the one-day M1. M1 controls growth by publishing deliberately, plus the floor.
- **Automatic `_suspect` repair calls (correctness critique 23):** rejected as gold-plating. A cache names the bad object, and `verify --repair` (M2) fixes it on Chopin.
- **"Unrecorded" Chopin commits (correctness critique 22) and the index and catalog races (correctness critique 14):** made moot by a single publisher plus the separate `push`.
- **The network-filesystem guard with the right fstype names (correctness critique 27):** dropped instead (fit critique 12). There is nothing to guard.
- **`--publish` inside vm-update and the `.vm-store.json` refusal (correctness critique 6, fit critiques 3 and 22):** both dropped. vm-update is not changed.
- **D4 and `--compare-host` / `also_seen` (correctness critiques 26 and 28, fit critique 19):** dropped. The state-less guests go in by explicit name, and an `i` VID becomes the `g` VID by sha match (§1.6).
- **Critiques taken in full:**
  - Correctness critique: 2, 3, 4, 7, 8, 10, 11, 12, 13, 15, 16, 17, 18, 19, 20, 21, 24, 25, 28.
  - Fit critique: 1, 2, 5, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17, 18, 20, 23, 24, 26.
  - Root handling, pins and SticTest coverage are deferred to M3 together with the wiring they belong to.
