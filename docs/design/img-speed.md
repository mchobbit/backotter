# Spec: faster rescue-image rebuilds, and farm balance

Status: **approved.** The seven speedups were approved in principle by the user
(2026-11-26, "Yes to all 1-7"), and every recommendation of §13 (D1-D17,
FD1-FD6) on 2026-11-26 ("Approve all"), D6 as the standing rule (each round
deletes `images/P/prev-*` and `prev-x*` sets older than the last two). I1, I2 and I5 are built (2026-11-26); no other I step is. Reviewed against the scripts on
2026-11-26 (§16 lists what the review changed). Hosts: chopin, cinqcent (big), copland,
cage, czerny (small, build role), choubox (small, no build role), all amd64.
Bigfoot (arm64) is out of scope except where noted. est. marks an estimate;
every other figure was measured on 2026-11-26 from the logs and file times
named beside it.

The seven, as approved:

1. **refresh-binaries mode**: when only the shipped binaries changed, take
   the last good image, swap them in, re-sign where needed, rerun the boot
   tests; OS updates on a slower cadence. Needs a provenance record per image.
2. **image lanes on more hosts**: seed the build inputs onto the small hosts.
3. **per-image input digests**: rebuild only the images whose digest changed.
4. **no compression for farm test images**: compress at publish.
5. **cross-compile bko-holt** for FreeBSD, NetBSD, OpenBSD, DragonFly and
   illumos (Haiku if feasible) on Linux; the VM build stays the reference
   until the cross build is proven equivalent.
6. **boot tests in parallel**, from the RAM disk.
7. **proof cells spread over all six amd64 hosts**.

§12 adds the farm-balance audit: everything else that still favours Chopin
and Cinqcent, with a proposed step for each finding.

## 0. Goal

On 2026-11-26 the rescue images were rebuilt twice. Both rebuilds changed
only bko-holt: bko-bin stayed round rel16 (`result.txt`: "bko-bin set from
round rel16, assembled 2026-11-24 13:55 UTC"), the Ubuntu base stayed
`slim-20261120`, and no builder guest changed. Each time all seven images
were still built from scratch on Chopin, compressed with xz on Chopin's two
threads, copied to Cinqcent, and tested only on Chopin and Cinqcent.

What the user wants instead:

1. A set whose OS bits did not change costs minutes, not hours, and does not
   heat Chopin.
2. Image work and its proof cells use the whole farm.
3. **No speedup may let an image ship binaries or OS bits that were not
   tested.** Every new image (every new SHA-256) still passes the full proof
   it passes today. A step that cannot prove this does not land (§2).

## Non-goals

- **Not a new image format.** Partition layouts, boot code, menus and the
  marker stay as they are. One exception: refreshed images get a new
  `image_id` and the labels derived from it are rewritten (D2).
- **Not fewer proof cells.** ImgTest2's boot cells and the HoltTest2 +img
  panel run for every new image. Only an image that is reused unchanged
  (the same SHA-256) skips them, because its rows already exist.
- **No change to what a full build contains**, apart from the free-space
  headroom that D1 adds to the BSD root file systems so that a later swap
  fits.
- **No change to vm-store's D2** (only Chopin talks to Bigfoot), to the fleet
  lock, or to the user's rule that guests are updated on Chopin, unless the
  user rewords it (FD1).
- **No release process.** "Publish" (§7) means a compressed, checksummed
  copy that the user asks for. Hosting it anywhere is out of scope.

## 1. Facts this spec rests on

### 1.1 The pipeline today

- `stick/img-lanes.sh "P P" ["P P"...]` runs on one host and deals the lanes
  only within that host. Each image is `stick/img-one.sh P` under
  `cmp-run.sh --reserve D 10`: D from `images/P/peak-kib`, 10 GiB of memory.
  So a small host (memory ceiling 10.1) fits one image build at a time.
  Chopin's own `~/backotter-work/images/lanes.sh` hard-codes three lanes
  (`linux haiku`, `openbsd netbsd dragonfly`, `freebsd illumos`).
- `img-one.sh P` moves `images/P/*` to `prev-STAMP/`, then writes
  `bko-bin.sha256` (the image's restic-bko, rustic-bko and bko-holt) and
  `bko-holt.commit`, builds the **release** image and then, as a second full
  build, the **--test** image (Linux: serial console default, 3 s timeout;
  BSDs: `TEST=1`, shorter menus). It boot-tests the test copy **one mode
  after another** (Linux: bios, uefi, uefi-secureboot, cdrom; the others: bios,
  uefi). It refuses an image whose binaries changed during the build. After a
  pass it runs `compress-images.sh -p P` (xz; `-T2` on Chopin, else `-T4`):
  the current release image gets a `.img.xz` beside it, and the `prev-*`
  images are replaced by `.xz`. `done.exit` is written last. **The only
  writer of `images/P/SHA256SUMS` is `compress-images.sh`**, and that file is
  where facts take the release image's SHA-256 (`facts_host.one_image`), which
  ImgTest2's key reads as `PLAT/release-image` (`img/spec.json`, `keys.py`).
  §7 must keep it written when xz goes.
- The FreeBSD builder listens on the fixed port 2400, so one host runs at
  most one FreeBSD build at a time. The snapshot builders have their own
  fixed ports (netbsd 2410 and so on).
- **Linux**: `slim-base-layer.sh` (the updated, slimmed Ubuntu base, built
  rarely, shared at `server/slim -> slim-20261120`), then
  `build-backotter-layer.sh` (packages from the ISO's pool, plus over the
  network the archive's release pocket, its -updates and -security pockets,
  and `bcachefs-tools` and the DKMS module's source from apt.bcachefs.org,
  checked against `stick/keys/apt.bcachefs.org.asc`; the drop-ins of
  `stick/layer/`; a DKMS bcachefs module built against -updates' kernel
  headers; and the three binaries in `/usr/local/bin`, checked by
  `--version` and by `bko-holt --version --json`'s features, which also
  writes the layer's `holt-api.json`), then `make-img.sh` (xorriso
  replays the ISO's signed boot setup, about 5 s). The config partition
  (256 MiB FAT, `marker.py`'s `config.img`) carries copies of the layer's
  bko-holt and rustic-bko.
- **The others** (`make-img-bsd.sh`): inside a VM of the platform. FreeBSD
  uses the long-lived `freebsd15-builder` (`BUILD_VM_MODE=running`): a
  per-host full folder, not in vm-store (HOSTS.md), whose qcow2 is written
  at every boot. The rest boot a throwaway snapshot of a test guest
  (netbsd11, openbsd8, dragonfly6, omnios58, haiku6), which vm-update and
  vm-store change underneath. `fetch` downloads and verifies the official
  media into `img-media/P`. `build.sh info` and `build.sh assemble` run in the
  VM. `info` prints the kernel and ZFS versions that `marker.py` writes into
  the marker; on FreeBSD it is also where `freebsd-update -b` updates the
  image's root over the network.
- **Then, by hand**: `img-xcopy.sh cinqcent P...` (it also compresses on the
  destination, and brings DEST's `holt-bin/P` to the image's bko-holt, but
  not its bko-bin). `promote-bins.sh --sets holt-bin` goes from Chopin to the
  other hosts. promote-bins and `img-refresh-holt.sh` hold `images/.lock-P`
  only while they install P's files: nothing stops either between two image
  builds of the same round. ImgTest2 (`t2 ImgTest2`) pins each host's test image
  (`tool_img.py`: a hard link, a `release_same` check on Linux) and boots it
  again (`testvm/test2/img/boot.sh`, again mode after mode, under
  `--reserve 1 5`). The HoltTest2 +img panel then runs one HoltTest line per
  platform with the image's own bko-holt.
- **Keys**: an img cell's and a +img line's key includes the pinned test
  image's SHA-256 (`keys.py`, `@image` -> `rescuer/img-PLAT`). A new image is
  therefore always due, and a reused image never is.

### 1.2 Measured: the second set of 2026-11-26 (Chopin, three lanes)

From the file times in Chopin's `~/backotter-work/images/P/` and `P/test/`
(local time, UTC+1). Release = `build.log`; test = `test/build.log`; boot =
the last `boot-*.out`; compress = `compress.log`. Minutes:

| platform | release build | test build | boot tests | compress | total |
|---|---|---|---|---|---|
| linux | 5.4 (layer 5.3) | 0.1 | 3.0 (4 modes, 40-48 s each) | 10.1 | 18.5 |
| freebsd | 5.4 | 3.0 | 1.7 | 5.1 | 15.2 |
| dragonfly | 2.4 | 3.0 | 2.8 | 7.2 | 15.4 |
| netbsd | 1.2 | 1.3 | 1.3 | 3.3 | 7.1 |
| openbsd | 1.9 | 2.0 | 1.1 | 5.3 | 10.3 |
| illumos | 7.3 | 7.7 | 1.8 | 1.8 | 18.6 |
| haiku | 1.1 | 1.1 | 0.8 | 4.4 | 7.5 |
| **sum** | 24.7 | 18.2 | 12.5 | **37.2** | 92.6 |

- The lanes ran 18:59:23 to 19:36:37 (37 min). The first set ran 38 min
  (12:38:39Z to 13:16:33Z, five platforms in lanes, with Haiku and NetBSD
  beside them), and its xcopy to Cinqcent took 11 min (13:16:33Z to 13:27:47Z,
  compression on Cinqcent included).
- **Compression is 40 % of the image-minutes.** It saves little on Linux: xz
  takes the 1.28 GB image only to 0.99 GB, because the squashfs inside is
  already compressed. The BSD images shrink 7-9 times (FreeBSD: 1.21 GB to
  166 MB). Nothing in the repo reads a `.img.xz`. Boot tests, QEMU and
  writing a stick all read the raw image.
- **The whole chain**, from the HoltTest2 builds to the end of the panel
  (`prep-og/images2-*.log`, `img2-*.log`): bko-holt builds 18:49-18:58 (9 min;
  dragonfly was the longest, about 9 min; linux 1.3 min), image lanes 37 min,
  holt-bin promote 1 min, ImgTest2 19 min (it ran only on Chopin and Cinqcent
  and lost 6-7 min to a host hold), panel 26 min (cinqcent and chopin only).
  That is about 103 min of active work, and 1 h 58 min from start to end.
- Chopin's images take about 71 GB (du, 2026-11-26), on a P3 that is 81 % full (349 GB free).
  Linux alone uses 30 GB, almost all of it in `prev-*` images that xz cannot
  shrink.

### 1.3 Where the binaries live in each image (what a swap touches)

| platform | the binaries | file system | id sites (derived from `image_id`) |
|---|---|---|---|
| linux | layer squashfs `/usr/local/bin/{restic-bko,rustic-bko,bko-holt}`; copies of bko-holt and rustic-bko in config.img | squashfs in the ISO 9660 tree | `/.disk/backotter.json`, config.img |
| freebsd | `root.ufs: /usr/local/bin/*` | FFS2 made by `makefs` with no free-space option (sized to fit) | the UFS label `BACKOTTER-<8 hex>`, `/etc/fstab` **and** `conf/base/etc/fstab` (a hard link made by `pax -l`: rewrite in place, never by rename), `/.disk/backotter.json`, config.img |
| netbsd | the root's `/usr/local/bin/*` | FFS2 made by `makefs -b 10% -f 10%` | `/.disk/backotter.json`, config.img; random GPT disk and partition GUIDs (`gpt create`/`add`) |
| openbsd | the root's `/usr/local/bin/*` | FFS (`newfs -m 0 -o space` on a vnd), partition `kb + kb/8 + 32 MiB` | `/.disk/backotter.json`, config.img; **the disklabel's random DUID, which `/etc/fstab` mounts root by** |
| dragonfly | `usr/local/bin/*` | UFS (`newfs -m 0`), sized from the binaries (`ours_kb`) +10 % + `ROOM_MB` 256 | the disklabel64 label `BKO_<32 hex>` (slice 2's label, not the file system's), `etc/fstab` and `boot/loader.conf`'s `vfs.root.mountfrom`, both by that label, `/.disk/backotter.json`, config.img |
| illumos | `usr/local/bin/*` **inside** `boot_archive.gz` (gzipped UFS miniroot, +10 % + 48 MiB) on the V_ROOT UFS | UFS in UFS, s2 sized +25 % +16 MB | `.volsetid` (on s2 only: the archive has none, I1's listing), the marker in the archive and on s2, `boot_archive.hash` (SHA-1 of the uncompressed archive, always present), config.img; random GPT GUIDs (`mkgpt.py`) |
| haiku | `system/non-packaged/bin/{rustic-bko,bko-holt}` | BFS, sized from the binaries +10 % + `ROOM_MB` 256 | `/.disk/backotter.json`, config.img |

On Linux the files a refresh rewrites, besides the binaries and the marker,
are the layer's squashfs and `.size`, `md5sum.txt`, and the appended config
partition; `make-img.sh` writes them all.

The table comes from `stick/*/build.sh` (grep for `bko-holt`, `image_id`,
`fstab`, `duid`, `uuid`). I2 must confirm it platform by platform: the
tree-diff check in §2 C2 fails any refresh that touches a path outside its
platform's list.

### 1.4 Signed objects

The Linux image boots with Secure Boot through Canonical's shim, GRUB and
kernel. `make-img.sh` replays them from the ISO, and `casper/vmlinuz` comes
from slim. The binaries live in a squashfs, which Secure Boot does not check.
The DKMS bcachefs module is unsigned, so it loads only with Secure Boot off.
**So a binary swap re-signs nothing today.** The other images are not tested
under Secure Boot. The refresh still checks that every signed object is
byte-identical to its parent's (C3), so that a later change that signs
something of ours cannot pass through a refresh unnoticed.

### 1.5 Where an image's OS bits come from

Some inputs can be hashed: the ISO, slim, the media, the builder guest's
vm-store generation and the stick/ files. Others arrive over the network at
build time: Linux's layer from the -updates and -security pockets and from
apt.bcachefs.org (a pinned `BCACHEFS_DEB_VERSION`, but a third-party
repository), FreeBSD's `freebsd-update -b`, OpenBSD's syspatches (their
signed list is fetched afresh by every `fetch`), and illumos packages from
the repository under its pinned incorporation. NetBSD and DragonFly have no
binary updates. Haiku's updates come from the haiku6 guest's packages, so
they follow its generation. **A digest therefore cannot prove that a Linux,
FreeBSD, OpenBSD or illumos base is current.** The planner never runs
`fetch`, so new media, syspatches or a moved kayak branch are seen only by
the next full build. The OS-update cadence (D3) decides when the network is
consulted again.

Some image bytes come from **host tools**, not from any hashed file: on
Linux `make-img.sh` runs the host's xorriso, mksquashfs, mkfs.vfat and
mtools, and `marker.py` makes every platform's config.img with the host's
mkfs.vfat and mtools. The farm runs one Debian release, but these packages
are updated host by host (§3.2, D15).

### 1.6 The bko-holt builds

`build-holt.sh` builds natively in a throwaway guest, with **the guest's
own packaged Rust**. HoltTest2's `info.json` of 2026-11-26: linux 1.94.1
(the pinned rustup toolchain), freebsd and openbsd 1.98.1, illumos 1.100.0.
So the shipped BSD binaries are built today by an unpinned compiler, which
changes whenever the guest is updated. DragonFly's rustic-bko is already
cross-built on Linux (`stick/dragonfly/build-rustic.sh`: a pinned nightly,
`-Zbuild-std`, clang and lld against a sysroot copied from dragonfly6),
and build-round's arm64 check already compares a native build with a cross
build (`linux-arm64-check`). holt/ has no dependencies.

## 2. Correctness rules

These bind every step below. A step lands only with the proof named for it
in §14.

- **C1. Tested binaries only.** An image carries exactly the restic-bko and
  rustic-bko of bko-bin's `SHA256SUMS` and the bko-holt of holt-bin's
  `PLATFORM/COMMIT` and `SHA256SUMS`. This is what `bko-bin.sha256`,
  `bko-holt.commit`, ImgTest2's `manifest:bins` and `img_refuse_pass` (the
  binaries' PASSes) check today. Refresh, reuse and cross builds keep every
  one of these checks. A cross-built bko-holt ships only once HoltTest2
  tests that very build (§8).
- **C2. A refresh changes only what it says.** The image's OS file systems,
  listed as path, type, mode, owner, flags or xattrs, size and SHA-256, must
  equal the parent's except on the platform's swap list (§1.3: the binaries,
  the marker, the id sites). Every refresh checks this, and a difference
  anywhere else fails it. The layout, partition table, boot code and ESP are
  compared byte for byte, except for the partitions the swap rewrote.
  The random ids a build makes (§1.3: GPT GUIDs, OpenBSD's DUID) are masked
  in that comparison, because the refresh makes them anew (D2). The release
  and test children must also differ from each other exactly as their
  parents did, ids aside.
- **C3. Signed objects untouched.** On Linux: the El Torito boot images, the
  appended EFI partition, `casper/vmlinuz` and `casper/initrd` match the
  parent's SHA-256. A refresh whose change list includes a signed object
  refuses and asks for a full build.
- **C4. Only a proven parent.** A refresh starts from the release and test
  pair of one provenance, current on its host, whose test image's ImgTest2
  row **and** +img panel row are PASS in the ledger for its SHA-256, not
  merely `done.exit 0`. (The release image is never booted, as today; C2
  ties it to its tested sibling.) Its provenance names the parent. A parent
  that is itself a refresh is allowed: the cadence (C5) bounds the chain.
- **C5. Same base.** A refresh needs the parent's base digest (§3.2) to
  equal the base digest that would be built now, and its OS epoch to be
  within the cadence (D3). Anything else is a full build.
- **C6. Same proof for every new image.** A refreshed image is a new
  SHA-256, so its img cells and +img lines are due (§1.1, keys). Nothing here
  marks a cell PASS without running it.
- **C7. Same inputs wherever it builds.** A host builds only if its inputs'
  digests equal the plan's (§5). The plan computes them on that host, from
  the files it will actually read. **The plan's** inputs are the newest:
  for a test guest, the newest vm-store generation (Chopin's working copy
  when it is ahead of the store); for slim, Chopin's `server/slim` link; for
  stick/, the synced commit; for bko-bin and holt-bin, the sets every
  builder holds (§5.2). A host that differs gets no unit of that platform
  and is named. **And at build time**: `img-one.sh` reads the builder's
  identity again once its VM is up and once the build ends, as it already
  rereads the binaries, and refuses the image if it moved (a vm-store link
  repointed, a vm-update committed) or if it is not the plan's. For the
  duration of its VM, `make-img-bsd.sh` holds the guest's
  `vms/G/.vm-update.lock`, so that vm-update refuses that guest instead of
  committing under a snapshot boot. Today vm-update's message would then say
  "another vm-update is working on it", so I1 rewords it. As built:
  `make-img-bsd.sh` takes the lock before it boots the snapshot and keeps it
  until it has shut the VM down (the VM itself does not inherit it, so that
  the next copy's build is not refused by the last one's QEMU on its way
  out), waiting up to a minute for a holder; `img-one.sh` holds the FreeBSD
  builder's for as long as it runs. `make-img-bsd.sh` rereads the identity
  when the VM is up and before it moves the image into place
  (`BKO_BUILDER_ID`); `img-one.sh` checks a plan's identity at its start
  (`BKO_PLAN_BUILDER`), and at its end `img_inputs.py moved` refuses the
  build when its stick/ inputs, its builder, the ISO, slim, the host tools
  or the binaries differ from the digest taken at its start (the media
  aside: the build's own fetch sets them).
- **C8. What is tested is the raw image.** Publishing compresses a copy and
  checks that the decompressed copy has the raw image's SHA-256, as
  `compress-images.sh` does today.
- **C9. Locks and order as today.** `images/.lock-P`, done.exit written last,
  and img-xcopy's move-then-done.exit protocol stay the only way an image
  directory changes. A retest (§6) follows them too.
- **C10. One round at a time, and no promotion inside one.** `img-round.sh
  run` holds a farm round lock: a file on the Mac, and `images/.round.lock`
  on each host it uses, from facts to the end of fan-out. While a host's
  round lock is held, `promote-bins.sh` and `img-refresh-holt.sh` refuse
  that host, as they refuse a held `images/.lock-P` today. Each unit is also
  started with the plan's bko-bin and holt-bin `SHA256SUMS` digests, and
  `img-one.sh` refuses at its start when the host's sets differ. Without
  this, a promotion between two units of one round yields a set whose
  platforms were built from different binaries (D16).

## 3. Provenance and input digests (the base for speedups 1 and 3)

### 3.1 `images/P/provenance.json`

`img-one.sh` writes it last, before `done.exit`, for both full builds and
refreshes (`stick/img_inputs.py provenance`; a failure to write it is said
in `result.txt` and leaves the image as built, without one, so a plan gives
it a full build). img-xcopy copies it with the image. Beside each image is
its tree listing, `images/P/os-tree.txt` and `images/P/test/os-tree.txt`
(I1, below): what C2 compares and `os_manifest` hashes. Schema 1:

```
{
 "schema": 1, "platform": "freebsd", "mode": "full" | "refresh",
 "built": {"host": "copland", "when": "2026-11-27T09:12:03Z", "run": "img-RUN",
           "stick_commit": "d66770a", "stick_dirty": false},
 "release": {"file": "backotter-live-...img", "sha256": "...", "size": 1208013312, "image_id": "..."},
 "test":    {"file": "test/backotter-live-...img", "sha256": "...", "image_id": "..."},
 "base": {
   "digest": "sha256 of the canonical JSON of inputs below",
   "inputs": {"stick": {"stick/make-img-bsd.sh": "sha256", "stick/freebsd/build.sh": "...", ...},
              "media": {"FreeBSD-15.1-RELEASE-amd64-memstick.img": "sha256", ...},
              "builder": {"vm": "freebsd15-builder", "identity": "see §3.2"},
              "host_tools": {"xorriso": "dpkg version", "squashfs-tools": "...", "dosfstools": "...", "mtools": "..."},
              "iso": "sha256 (linux)", "slim": {"path": "slim-20261120", "manifest": "sha256 of its file list"}},
   "os_epoch": "2026-11-27",   // when this base last took updates from the network (full builds only)
   "os_manifest": "sha256 of C2's tree listing, the swap list left out"
 },
 "bins": {"digest": "...", "restic-bko": "sha256", "rustic-bko": "sha256", "bko-holt": "sha256",
          "bko_bin_round": "rel16", "holt_commit": "40 hex", "holt_code": "64 hex", "holt_build": "vm" | "cross"},
 "tests": {"digest": "...", "boot": {"bios": 0, "uefi": 0}},
 "parent": null | {"sha256": "...", "image_id": "...", "provenance_sha256": "..."}
}
```

`os_manifest` is what C2 compares. A refresh copies `os_epoch` and
`os_manifest` from its parent unchanged. If the swap were to change them,
C2 would already have failed it.

As built in I1: `base.trees` adds, for the release and the test copy, the
listing's `os_manifest`, its entry count, its tree names and `swap_missing`
(the swap-list paths, not marked optional, that the listing lacks: empty on
all seven in I1's set). `test.os_manifest` is the test copy's. `built.run`
is null when img-one.sh is given no `BKO_IMG_RUN` (img-lanes.sh passes none
yet). The provenance is not written (said in `result.txt`) when the build's
stick/ inputs, builder, ISO, slim, host tools or binaries moved since its
start (`--start`), so that a vm-update or a sync during the boot tests cannot
record inputs the image was not built from. `holt_code` is null until
holt-bin records the CODE a bko-holt was built from (today only `COMMIT`). `tests.tools` holds the QEMU and OVMF
versions.

**The tree listing.** One line per entry, tab-separated: tree, path, type
(f d l c b p), mode, uid:gid, size, SHA-256 (a symlink's target, a device's
major,minor), and the file flags and xattrs tar recorded. A hard link is
listed as the file it links to. Linux: `img-one.sh` extracts the image's
ISO 9660 tree with xorriso (tree `iso`, owners left out) and the BackOtter
layer's squashfs from it as root (tree `layer`, with xattrs), about 3 s an
image. The VM platforms: a fourth `build.sh` subcommand, `build.sh list`,
run in the build VM after `assemble`, attaches `out/image.img` read-only
with the platform's own tools, mounts its OS file systems read-only and
writes them to stdout as a tar (illumos with python3's tarfile, its own tar
being unreliable for this); the host lists the tar. Trees: `root` on the
four BSDs, `stick` (s2) and `archive` (the boot archive) on illumos, `bfs`
on Haiku. Haiku's GNU tar records no BFS attributes, so Haiku's listing has
none. I4's refresh reuses the attach code. The swap list (§1.3) is
`stick/img-swap.tsv` (platform, tree, path glob, kind, optional).

### 3.2 The three digests

- **base**: the stick/ files the platform's build reads, its media, its
  builder guest's identity, the host tools that write image bytes and, on
  Linux, the ISO and slim. This is a fixed per-platform list in
  `stick/img-inputs.tsv` (platform, glob, kind), so that a change to
  `stick/linux/` never rebuilds Haiku. Every platform lists `img-one.sh`,
  `marker.py` and `stick/config-partition/*` (config.img's README and
  settings). The VM platforms add `make-img-bsd.sh`, `stick/bsd/*`,
  `testvm/vm-run` (which reads no other testvm/ file, so `vm-lib.sh` is not
  an input), and all of `stick/P/` except the test files
  below (`platform.conf` pins the release, its keys and kayak's commit;
  `build.sh`, `mkgpt.py`, `hook.sh` and the BUILD_FILES). Linux adds
  `build-backotter-layer.sh`, `make-img.sh`, `stick/layer/**` and
  `stick/keys/*`. The media part is the list the last `fetch` set (recorded
  in `img-media/P/MEDIA` by `make-img-bsd.sh`), hashed by content, not
  whatever else the directory holds. **A lint in I1 fails when a file under
  `stick/` is neither an input of some platform nor listed in the tsv as
  `none` (with a reason)**, so a new script cannot be missed silently. The
  digest is taken over file contents, never over the git commit: a commit
  that touches no input changes no digest. As built: the tsv's columns are
  PLATFORMS (`all`, `vm`, `-` or names), GLOB (`**` crosses directories),
  KIND (`base`, `tests` or `none`) and REASON; for each platform the last
  matching row decides, so `stick/P/**` is taken back by later `tests` and
  `none` rows. The lint (`img-digest.sh --lint`, and a t2 unit test) also
  fails on a glob that names no file and on a `BUILD_FILES` entry that is
  not its platform's base input.
- **The builder's identity.** For a snapshot builder (a test guest), its
  generation, which is the same in Chopin's working copy and in a vm-store
  copy reached through the `vms/G` link: `vms/G/image-state`'s
  `generation`, while `image-stat.json` still records the qcow2's present
  (size, mtime, inode) at that generation. A guest whose disk changed since
  falls back to its qcow2's SHA-256, cached by (size, mtime, inode)
  (`BKO_DIGEST_CACHE`, default `BKO_WORK/.img-digest-cache.json`, which
  also caches the media, the ISO and slim's files), because hashing a
  multi-GB qcow2 per plan is slow. **FreeBSD's
  freebsd15-builder has neither**: it is a per-host folder written at every
  boot, so its qcow2 hash would change every build and differ on every
  host, and FreeBSD would never refresh or build off its first host. Its
  identity is a fingerprint the build takes from the running builder and
  writes to `vms/freebsd15-builder/fingerprint`: `freebsd-version -ku` and
  the SHA-256 of the base tools the build runs (makefs, mkimg, mdconfig,
  gpart, tar, pax, freebsd-update). The planner reads that file; a refresh
  or build that finds the live fingerprint different stops before it writes
  (C7). Hosts whose builders differ therefore show up in `plan`. As built,
  a build with no plan (`BKO_PLAN_BUILDER` unset) takes the live
  fingerprint and rewrites the file when it differs, so the file follows the
  builder; with a plan, a live fingerprint other than the file's refuses.
  Either way the fingerprint taken again at the end of the build must equal
  the first.
- **Host tools**: on every platform the dpkg versions of `dosfstools` and
  `mtools` (config.img), and on Linux also of `xorriso` and `squashfs-tools`.
  They are recorded in provenance, and whether they enter the digest is D15.
- **bins**: the three files' SHA-256 (two for Haiku).
- **tests**: `boot-test.py`, `stick/P/checks.sh` and `config-checks.sh`,
  `manifest.sh`, and `img-one.sh`'s mode list (its `MODES_linux` and
  `MODES_other` lines). The host's QEMU and OVMF
  versions (`uefi-secureboot` boots OVMF's MS-keyed variables) are recorded
  in provenance's `tests` but kept out of the digest: they differ host by
  host, and ImgTest2 boots every new image on each host's own anyway. When
  only this digest changes, the image is not rebuilt. Its boot tests run
  again on the same image (§6).

`stick/img-digest.sh P` prints the three digests and the input list as JSON
on any host. It reads only, and never touches `images/` (it runs
`stick/img_inputs.py`, which also lints, lists trees and writes the
provenance).

### 3.3 The planner's actions

Per platform, the current inputs are compared with the provenance of the
current image that passed its proof (C4):

| base | bins | tests | OS epoch | action |
|---|---|---|---|---|
| same | same | same | within cadence | **reuse**: no build, no cells due |
| same | same | changed | within cadence | **retest**: boot tests on the current image |
| same | changed | any | within cadence | **refresh** (§4) |
| changed | any | any | any | **full** |
| any | any | any | past cadence | **full** |
| no provenance (an image from before I1) | | | | **full** |

`--full P` forces a full build. `--no-refresh` turns refreshes into full
builds for a round (a debugging escape).

## 4. Speedup 1: refresh-binaries

### 4.1 Linux

`build-backotter-layer.sh --refresh-from PARENT_IMG SOURCE_ISO SLIM_DIR OUT_DIR`:

1. Check C5: the parent's provenance names the same ISO SHA-256 and slim
   manifest.
2. Extract the parent's `casper/ubuntu-server-minimal.backotter.{squashfs,manifest,size}`
   with `xorriso -osirrox`, as make-img.sh already extracts grub.cfg, then
   `unsquashfs` it as root. That keeps owners, xattrs and the overlay
   whiteouts, which are character devices.
3. Replace the three files in `usr/local/bin` (`install -m 755 -o 0 -g 0`).
   Run the checks `build-backotter-layer.sh` runs on them today, by the same
   code (factored out, not copied): ELF machine and static linking (`:150`),
   `--version` of each (`:407-412`), and `bko-holt --version --json` **in the
   unpacked tree's chroot**, which writes `$LAYER.holt-api.json` and must list
   `dump_schemas`, `luks_headers` and `snapshot_layout` (`:423-433`). Then
   `make-img.sh` takes that file as it does today. Write `report.txt`'s
   "Archivers, and bko-holt" lines the same way, keeping the parent's package
   section.
4. Run `mksquashfs` with the options `--settings` prints, then
   `.size`. The manifest (the package list) is the parent's, unchanged.
5. `make-img.sh` and `make-img.sh --test` from that layer, unchanged. They
   write a new marker, a new config.img with the new bko-holt and rustic-bko,
   and md5sum.txt.

The tree listing (C2) compares the parent's and the child's layers: only the
three binaries may differ. The base squashfs, kernel and initrd are the
same files from slim, and their SHA-256 inside the image is checked (C3).
Est. 1-2 min (a 69 MB squashfs, 474 files) against today's 5.4 min, and
nothing comes from the network.

### 4.2 FreeBSD, NetBSD, OpenBSD, DragonFly, illumos, Haiku

A third subcommand beside `info` and `assemble` in each `stick/P/build.sh`:
`build.sh refresh`, run in the same VM by `make-img-bsd.sh --refresh-from
PARENT_IMG`. `in/` holds `parent.img` (attached as a second disk where vm-run
can do so, else copied in the way the output is copied out today), the new
binaries, the new `backotter.json` and `config.img`. The subcommand:

1. Attaches the parent with the platform's own tools (mdconfig, vnconfig,
   vn, lofiadm, Haiku's mount of an image), checks it with fsck, and mounts
   the file system that holds the binaries read-write. On illumos, it
   gunzips `boot_archive.gz` and mounts that as well.
2. Writes the tree listing before the change. Replaces the binaries and the
   marker. Rewrites the platform's id sites (§1.3, D2): on FreeBSD `tunefs
   -L` and both fstabs, in place; on DragonFly the disklabel64 label and the
   fstab and loader.conf lines that name it; on OpenBSD a new DUID
   (`disklabel -R` from the parent's label with a fresh `duid:`) and the
   fstab line; on illumos `.volsetid` on s2 (the archive has none). Writes the
   listing after the change.
3. Unmounts and runs fsck again. On illumos it runs `gzip -9` on the archive,
   puts it back on s2 and rewrites `boot_archive.hash` (the SHA-1 of the
   uncompressed archive, `build.sh:170`). On NetBSD and illumos the host
   then gives the GPT new random disk and partition GUIDs, so that the child
   shares no id with its parent.
4. Refuses, with its own exit code, when the new files do not fit. The
   planner then falls back to a full build of that platform.

**The new marker.** `marker.py` runs on the host as today, but with the
parent's `base_os`, `base_release`, kernel, ZFS version and ZFS feature list
(read from the parent's `backotter.json` and from the parent tree's
`usr/share/zfs/compatibility.d/` file), because `build.sh info` cannot be
rerun: on FreeBSD it rebuilds the root from the memstick and runs
`freebsd-update` over the network. Only `image_id`, the build time, the
bins and `holt-api.json` are new. The holt-api comes from running the new
bko-holt in the VM, as `make-img-bsd.sh` does today. The refresh checks that
the parent's marker fields still match the parent's tree.

The host writes `config.img` into its partition with dd, outside the VM.
The partition is 256 MiB, at the offset the parent's partition table gives.
The host then compares both listings and the partition table (C2). The
release and test copies are each refreshed from their own parent, because
today they are two separate builds.

**Headroom.** Today's free space differs by platform (§1.3). DragonFly and
Haiku keep 10 % + 256 MiB, which is enough. FreeBSD's `makefs` is given no
free space, NetBSD keeps 10 %, OpenBSD `kb/8 + 32 MiB`, and illumos 48 MiB
in the archive and 25 % + 16 MB on s2. So I4 measures each image's free
space, and full builds get slack (D1) only where it falls short of
`max(64 MiB, 50 % of the binaries' size)`. That adds a few tens of MB to
some images, the one change to full builds that this spec makes.

Est. per platform: 1-2 min of VM boot, under a minute of swap and fsck per
copy, and no media fetch or OS updates. That is 3-5 min against 2.3-15.0 min
of building today.

### 4.3 Secure Boot

Nothing to re-sign today (§1.4). C3's check runs on every Linux refresh, and
the uefi-secureboot boot test runs as always. If BackOtter ever signs its
own objects (a MOK-signed module, a UKI), they go on the signed list, and a
refresh that changes one refuses until the signing step exists. That is a
later spec.

### 4.4 OS-update cadence

A full build is the only thing that takes new OS updates, so the planner
forces one per platform when (D3):

- its base digest changed (a new slim, new media, an updated builder guest,
  or a changed stick/ input), or
- its `os_epoch` is older than **14 days** (est. the same rhythm as
  CarpetBeater's twice-monthly refresh, which then rebuilds the full set), or
- the user asks: `img-round.sh --full all` after a security advisory.

`img-round.sh plan` prints each platform's epoch age, so a stale base shows
in every plan.

## 5. Speedup 2: image lanes on more hosts

### 5.1 Seeding the inputs

- **The BSDs, illumos and Haiku need nothing new** on copland, cage and
  czerny. Checked on copland: `vms/` has freebsd15-builder, netbsd11,
  openbsd8, dragonfly6, omnios58 and haiku6, and `img-media/` has a directory
  for each of the six. Cage and czerny were not checked: the facts step
  (§5.2) finds out, and a host that lacks a builder or its media gets no unit
  of that platform. Their 10G RAM disk (ceiling 9.0) holds the largest
  measured build peak (illumos 3.9 GB, so `--reserve` about 4.6). The
  builder's identity enters the base digest, so a host with an older
  generation is told so and gets no units (C7). Every small host's
  freebsd15-builder is its own (§3.2), so until they are brought alike the
  FreeBSD units stay on the hosts whose fingerprint matches.
- **Linux** needs `server/` on the small hosts: the 26.10 ISO (2.7 GB) and
  `slim-20261120` (2.2 GB). `stick/img-seed.sh HOST...` (from the Mac) copies
  them the way `shared-slim-path` says:
  `sudo tar --numeric-owner --xattrs --acls` over ssh into
  `/bkoscratch/imgbuild/server`, the `server` link, and `slim -> slim-YYYYMMDD`.
  It then checks the slim manifest (§3.1) against the source, and the host
  tools `make-img.sh` and the layer build need (squashfs-tools 4.6 or later,
  xorriso 1.5 or later, dosfstools, mtools, `sudo -n` for the layer build,
  and HTTPS to archive.ubuntu.com, security.ubuntu.com and
  apt.bcachefs.org). Whenever slim is repointed on Chopin, the same script
  repoints every seeded host, and the planner refuses a host whose slim
  manifest differs.
- **choubox** gets no build units. It has no build role, its RAM disk is 4G,
  and its memory ceiling of 6.0 is under img-lanes' 10.

### 5.2 The dealer: `stick/img-round.sh` (on the Mac)

```
img-round.sh plan  [--hosts H,H|all] [--full P,P|all] [--no-refresh] RUN
img-round.sh run   [same options] RUN
img-round.sh fanout [--to H,H|all] RUN      # gather and distribute only
```

1. **Facts.** One ssh per host, in parallel: `img-digest.sh` for each
   platform, the host's room (`cmp-run.sh --free`, `--ceiling`), its bko-bin
   and holt-bin `SHA256SUMS`, and its current images' provenance. Every
   builder's holt-bin and bko-bin must match, or the round stops and names
   the host (C1, C7).
2. **Plan.** §3.3's action per platform. A reused or retest-only platform
   gets no build unit.
3. **Deal.** The units go longest first to the host whose estimated finish is
   earliest, as `deal-units.py` deals a build round. Image units are added to
   its table with measured minutes: full builds from §1.2 minus compression,
   refreshes est. 3-5 min. Per host: one image unit at a time on a small host
   (memory 10 of 10.1), two on a big host (20 of about 23). At most one
   FreeBSD unit per host (port 2400). Hosts that lack an input are skipped.
   Chopin is penalised as `pick-host` penalises it. A small host's 10 GiB
   reservation waits for all of its memory, so the dealer prefers hosts whose
   room is free now (step 1's `--free`), and `run`'s summary names any unit
   that waited more than 5 min for its reservation. A refresh's real memory
   need is measured in I3 and I4; if it is well under 10 GiB, its
   reservation shrinks so that it fits beside a host's other work.
4. **Build.** Each host runs `img-lanes.sh --run RUN "P"` for its units.
   `img-one.sh` gains `--refresh-from` and the `BKO_IMG_COMPRESS=0` default
   (§7).
5. **Fan-out** (§10). Each built platform is xcopied from its builder to
   every other proof host.

`run` writes `RUN/plan.json`, `RUN/HOST.log`, and a one-screen summary for
each platform: the action, the host, the minutes and done.exit.

*As built (I2, 2026-11-26):* `img-round.sh plan` (`stick/img_round.py`, on
the Mac) asks each build-role host (D4; `--hosts` narrows) and the reference,
Chopin (`--ref`), over one ssh: pick-host's room probe, then
`stick/img_round_facts.py`, which runs that host's `img_inputs.py digest` for
all seven and reads `images/P` (provenance, done.exit, the test image's
SHA-256 from its ImgTest2 pin or hashed through the digest cache, SHA256SUMS'
release line, `peak-kib`) and the two sets' SHA256SUMS digests. The plan's
inputs are the reference's digests. C4 is read from the Mac's ledger: a
valid, committed `img:P` PASS of the test image's SHA-256
(`inputs.bin["P/test-image"]`) whose panel passed **or was not needed**
(storage pieces unchanged, or no panel), with no failing row of it since; a
panel owed, or a row that records no panel, is not proven. (The last set's freebsd row had no panel due, so a
panel PASS alone would never let it be reused.) The base is compared less its
media (`base_view`): `platform.conf`, a base input, pins them, a full build's
own fetch sets them (as `moved()` already takes them), and
`img-media/P/MEDIA` is written only by a host's own fetch, so on 2026-11-26
it would have refused every host but one per platform, and an image built
elsewhere would have looked changed on Chopin. A refresh is planned only
where the reference's stick/ has one (img-one.sh and
build-backotter-layer.sh taking `--refresh-from`, a `refresh)` in
`stick/P/build.sh`), else it is a full build that says why, and it goes only
to an able host that holds the parent, else full. A host's reservation is
img-lanes.sh's (`peak-kib`, else 6, linux 1; memory 10) against its ceiling.
The deal is `deal-units.py`'s `deal_images` (also `img-units`, `img-deal`):
hosts in hosts.py's tie order, a host's estimate times 1 + pick-host's load
a core (Chopin's +4), and 5 min more when a reservation doesn't fit its room
now. `plan` writes `RUN/plan.json` and `RUN/facts.json`, and exits 1 when a
unit has no host or the reference gave no digest of a platform (action
`none`); `run` and `fanout` say they are I8's. On the farm (2026-11-26, from a private stick/ copy)
`plan` took 23 s and said full ×7, "no provenance (an image from before
I1)": linux off the small hosts (no ISO, no slim: I7), FreeBSD on Chopin
alone (the only builder with a fingerprint), haiku off cinqcent (its haiku6
disk changed since its generation). With provenance written privately for
Chopin's current images (symlinks, the real ledger) it said reuse ×7, each
proven by the 2026-11-26 ImgTest2 rows. The literal proof, `plan` right after
a fresh set, waits for the first set built with I1's provenance.

### 5.3 What moves with it

- `promote-bins.sh`'s "the shared sets are assembled where the images are
  built", which makes the source big-class Chopin by default: the source can
  be any build host whose sets check (F20). holt-bin still comes from
  `img-refresh-holt.sh` on each builder, out of HoltTest2's builds, which
  HoltTest2 already copies to every run host.
- Chopin's `images/lanes.sh` and its stale copies of `img-one.sh` and
  `img-lanes.sh` are retired. The farm runs the synced `stick/` copies.

## 6. Speedup 3: rebuild only what changed

The planner of §3.3 is this speedup. For the two sets of 2026-11-26 it would
have planned 7 × refresh. For a stick/ change to `stick/netbsd/build.sh` it
plans one full NetBSD build and six reuses. For a `boot-test.py` change it
plans seven retests: boot tests on the current images, a new
`tests.digest`, and no new SHA-256. A retest writes its results into the
current provenance's `tests` and refreshes `test/boot-*.out`.

A retest changes the image directory, so it follows C9: it holds
`images/.lock-P`, removes `done.exit` while it runs (pins refuse the
platform meanwhile, as during a build), and writes `done.exit` last with
the count of failed modes. It runs on the builder's copy only. The fanned-out
copies keep their provenance until the next fan-out.

ImgTest2 needs no change, provided `images/P/SHA256SUMS` is still written
(§7). Its keys already follow the image's SHA-256 and its harness files, so
a reused image's cells stay PASS, and a retest's harness change makes them
due by the harness digest.

## 7. Speedup 4: no compression on the farm; compress at publish

- `img-one.sh` and `img-xcopy.sh` stop calling `compress-images.sh` by default
  (`BKO_IMG_COMPRESS=0`; `=1` restores today's behaviour). The farm keeps
  raw images only. That saves 37 image-minutes a set, 40 % (§1.2), and every
  xz on Chopin.
- **`SHA256SUMS` stays.** `img-one.sh` writes `images/P/SHA256SUMS` itself,
  in the same format, with the raw release image's line (and the test
  image's, in `test/`). Without it, facts lose the release image's SHA-256
  and ImgTest2's `PLAT/release-image` key goes blank (§1.1), so a new
  release image would no longer change the key. img-xcopy already copies the
  file. I5's proof checks that the keys are unchanged.
- **Publish**: `stick/img-publish.sh [--host H] SET`, which the user starts
  (or CarpetBeater's step does). On a host that is not Chopin (default: the
  least loaded build host), it copies the current release images of the set
  to `images/publish/SET/P/`, compresses them there with xz `-T0` under a
  reservation, and writes `SHA256SUMS` with both checksums after a
  decompress-and-hash check (C8). It never touches `images/P/` itself. It
  publishes only images that passed C4's rows, and it refuses (naming the
  platform) a set whose `os_epoch` is past the cadence (D3), so that a
  published image is never older in OS updates than the cadence allows.
- **Retention** replaces what compressing `prev-*` did for disk space (D6).
  Without xz, each set adds about 15.3 GB raw per holding host (7.66 GB of
  release images plus the same again in test copies). The proposal: keep the
  current image and the last two `prev-*` raw on every host, because the
  refresh parent and a rollback need them. Delete older `prev-*` and
  `prev-x*` directories only after the user's yes (D6). Retention never
  deletes a directory that a running refresh reads as its parent (it holds
  `images/.lock-P`), and a deleted image that an ImgTest2 pin still
  hard-links frees its space only when the pin moves. Published sets stay
  as `.xz`.
- **As built (I5).** `img-one.sh` writes `SHA256SUMS` once the build is
  checked, before the boot tests, whatever their verdict (compress-images.sh
  wrote it only after a pass); `BKO_IMG_COMPRESS=1` still compresses after a
  pass, adding the `.xz` lines; img-lanes.sh passes the variable through.
  The D6 rule is `stick/img-retain.sh [-n] [-p P] [IMAGES]`: per platform,
  under `images/.lock-P` (a held lock skips the platform and says so), it keeps
  the two newest `prev-*` sets by the stamp in their names (`prev-STAMP` and
  `prev-xSTAMP` alike; older hand-made names such as `prev-20261106-pass1`
  by their date), removes empty ones (img-xcopy makes one when DEST had
  nothing), and leaves any other name alone. It runs after every passing
  `img-one.sh` (inside the build's own lock), and on DEST after `img-xcopy.sh`
  copied a platform whose `done.exit` is 0 (from `BKO_DEST_STICK`);
  `BKO_IMG_RETAIN=0` skips it. A failed build runs no retention, so a run of
  failures cannot push the last good set out. `stick/img-publish.sh` runs on
  the Mac (`img_publish.py`: the ledger is there): it gathers img-round's
  facts from the build hosts other than the fleet host, and refuses `--host
  chopin`. The default host is the least loaded one (load a core) that holds
  a publishable image of every platform asked for (`--only P,P`); a platform
  publishable nowhere refuses the set by name. `stick/img-publish-host.sh`
  does the host's part: it hard-links (or copies) each release image into
  `publish/.SET.part/P/` under a shared `.lock-P`, checks its SHA-256 against
  the proven one, runs `xz -9 -T0` under `cmp-run.sh --reserve 0 8` (xz's
  memory limit a GiB under it, which caps its threads), checks the
  decompressed SHA-256 (C8), writes `P/SHA256SUMS` with both lines and drops
  the raw copy, then renames the whole to `publish/SET` with the Mac's
  `PUBLISHED.json` (what was proved, where). A set name is published once.

## 8. Speedup 5: cross-compiled bko-holt (and bko-rescue)

### 8.1 Build

`stick/holt-cross.sh PLATFORM` (and `HOLT_CRATE=rescue`, as `build-holt.sh`
does for bko-rescue) runs on any amd64 Linux host, in the RAM disk under a
reservation:

| platform | Rust target | std | toolchain (D10) | sysroot |
|---|---|---|---|---|
| freebsd | x86_64-unknown-freebsd | rustup, tier 2 | pinned 1.94.1 | from freebsd14 (so it runs on 14 and 15, as today) |
| netbsd | x86_64-unknown-netbsd | rustup, tier 2 | pinned 1.94.1 | netbsd11 |
| illumos | x86_64-unknown-illumos | rustup, tier 2 | pinned 1.94.1 | omnios58 |
| openbsd | x86_64-unknown-openbsd | tier 3: `-Zbuild-std` | a pinned nightly | openbsd8 (libc.so of that release: OpenBSD binaries link libc dynamically) |
| dragonfly | x86_64-unknown-dragonfly | tier 3: `-Zbuild-std` | DragonFly rustic's pinned nightly | dragonfly6 (the existing `dragonfly-cross` sysroot) |
| haiku | x86_64-unknown-haiku | tier 3 | needs Haiku's own cross GCC and libroot | **stays a VM build** (D9) |

The link goes through clang and lld with `--sysroot`, as DragonFly's
rustic-bko does. Each sysroot is copied once from a throwaway copy of the
guest, cached per guest generation, and refreshed by itself when the
generation changes. Paths are remapped (`--remap-path-prefix`) as
build-round's zig builds remap them. Est. 1-3 min per platform with no guest
booted, against today's 2.3-9 min in a guest.

### 8.2 Proof: E2, behaviour equivalence

The bytes cannot match, because the compiler differs (§1.6). Per platform,
before the cross build may ship:

0. **ELF shape**: `readelf -lnd` (and `-V`) of the cross build equals the VM
   build's, apart from addresses and sizes: static or dynamic, PIE,
   RELRO, the interpreter, `DT_NEEDED` with library versions, the symbol
   versions it needs, and the OS notes and segments (FreeBSD's `.note.tag`
   ABI version, which must be 14's; NetBSD's `.note.netbsd.ident` and PaX
   notes; OpenBSD's `.note.openbsd.ident`, `PT_OPENBSD_RANDOMIZE`,
   `PT_OPENBSD_WXNEEDED`, `PT_OPENBSD_NOBTCFI` and `endbr64` landing pads).
   **This is the only guard for OpenBSD's IBT enforcement**: OpenBSD kills
   a binary without landing pads or `NOBTCFI` on Intel CPUs that have IBT
   (Tiger Lake and later), and no farm host has such a CPU (the M900s and
   choubox's Comet Lake do not; est., to check for Chopin and Cinqcent), so
   steps 2 and 3 cannot catch it.
1. **API**: `bko-holt --version --json` of the cross build, run in a guest
   of the platform, equals the VM build's, apart from the build fields
   (`features`, `dump_schemas`, `platform`).
2. **Unit tests on the target**: `cargo test --no-run --target T` cross-builds
   holt's test binaries. They are copied into a throwaway guest of the
   platform and run there, and all of them must pass.
3. **HoltTest2 side by side**: every HoltTest2 guest of the platform (for
   FreeBSD that means freebsd14, freebsd15, -mirror, -geli, ghostbsd,
   hardenedbsd and the rest, the corpus's list) runs once with the VM build
   and once with the cross build, on the same guest generation, under a
   private T2_HOST_ROOT and ledger. Both must PASS, and their dumps must be
   equal after the judge's existing normalisation.
4. **Twice**: steps 0-3 must hold on two consecutive holt-change rounds
   (D11).

The first cross build may simply fail: the BSD and illumos `cfg` code has
only ever been compiled by the guests' Rust 1.98-1.100, never by 1.94.1, so
it may use a newer API. I10 finds out.

### 8.2a Keys: the cross build must be tested as itself

HoltTest2 keys a platform's bko-holt by its **code digest** CODE (R14,
`keys.py`: "built or not"), and `HOST_ROOT/holt-bin/CODE/` never has a
binary written over (`holt.py`). So a cross build of the same code would
find the VM build already in place, and every holt cell would stay PASS on
the VM build's rows. C1 would then be broken without any check noticing.
The cross build therefore gets a key of its own: a build-kind input in the
holt key (`PLAT/bko-holt-build`: `vm`, or `cross:` with the toolchain and the
sysroot's guest generation), and its own directory beside CODE's
(`holt-bin/CODE/cross/PLAT/`). I10's check builds go there, never to
`CODE/PLAT/`. At I12's switch the key changes, so every holt line of that
platform is due once and is run on the cross build. `img-refresh-holt.sh`
copies from the directory the key names. (A guest's Rust update changes
today's VM build without changing CODE either: a pre-existing gap, which
D10's pinned toolchain closes for the cross-built platforms.)

### 8.3 Rollout

- **I10/I11**: HoltTest2's build step makes both builds, VM and cross. It
  ships and tests the VM build. The cross build is a **check build**, as
  `linux-arm64-check` is, and E2 runs privately.
- **I12**: per platform that passed E2, HoltTest2 builds cross only. That
  build is the one tested (its own key, §8.2a) and the one
  `img-refresh-holt.sh` copies, so C1 holds. `provenance.bins.holt_build` says `cross`. The VM build runs as a
  **reference** once a week and at each change of the pinned toolchain or
  the guest generation, with E2's step 3 on one guest. A difference makes
  that platform go back to the VM build until it is explained.
- Cross builds need no guest and no big host, which also removes F8's
  "HoltTest2 needs a big build host".

Gain: about 5-7 min of wall time per holt-change round, because DragonFly's
9 min VM build is the longest today (est. 1-3 min cross). Up to six guest
boots per round leave the hosts, and the shipped compiler stops changing
whenever a guest is updated.

## 9. Speedup 6: boot tests in parallel, from the RAM disk

- `boot-test.py` boots each mode with its own copy-on-write overlay and its
  own OVMF variables copy, with no network ports (`-serial stdio`). Its
  modes can therefore run side by side on one read-only image. Each guest
  has 2 vCPUs and 4 GiB.
- `img-one.sh` and ImgTest2's `boot.sh` run the modes as parallel jobs. They
  first copy the test image into the reservation's RAM directory (up to
  1.6 GB), so the reads do not hit the P3, and overlays already go to TMPDIR.
  img-lanes' reservation is sized from the build's peak alone, and Linux's
  is 1.0 GiB (its build works on disk), which does not hold a 1.28 GB image:
  `img-lanes.sh size()` becomes the larger of the peak rule and the test
  image's size + 0.5 GiB.
- **Width.** As many modes at once as the reservation's memory holds at
  4.5 GiB each (D8: the guests keep 4 GiB). Inside img-lanes' 10 GiB that is
  two. ImgTest2's `--reserve 1 5` is raised to
  `--reserve (image + 0.5) (4.5 × width)`, with width 4 on a big host, 2 on
  a small one and 1 on choubox, and never more than the platform's modes
  (two for the BSDs). The planner picks the width from the host's ceiling.
  On a small host width 2 reserves 9 of its 10.1 GiB, so an ImgTest2 cell
  there runs beside nothing else heavy.
- The manifest, which the first mode that reaches the checks types, is
  written only by the mode named first. If that mode fails before the
  checks, the next mode that reached them is booted once more, alone, with
  `--manifest`, after the parallel batch, so that a manifest is still typed
  as in today's serial order (its verdicts stay those of the batch). Each
  mode's `.out`, `.log` and `.rc` go to their own files, as today.
- Gain: boot phases from 12.5 to about 6 image-minutes (est.: Linux 3.0 to
  about 1.0, the BSDs about halved), and the same again in each ImgTest2
  run.
- **As built (I6).** One runner, `stick/boot-modes.sh` (a `tests` input in
  `img-inputs.tsv`, and in ImgTest2's harness), serves both callers:
  `--width N`, `--ram DIR` (the copy, checked against `--sha256` and made
  only when the image + 0.5 GiB fits in what DIR has left of `--ram-gib`;
  otherwise the modes boot the image itself and `boot-modes.txt` says why),
  and `--manifest`. `img-one.sh` takes `BKO_BOOT_WIDTH` (default 1),
  `BKO_BOOT_RAM` and `BKO_BOOT_RAM_GIB`, which `img-lanes.sh` sets to 2, its
  RAM directory and its D. `img_round_facts.reserve_gib` mirrors the new
  `size()`. In ImgTest2, `img.boot_width` and `img.boot_reserve` give the
  job's `--width` and `--reserve` per host at its start (kept in the run's
  state); the planner keeps an img cell off a host whose ceiling can't hold
  width 1. An image size the facts don't give counts as 1.6 GB. A stop now
  unwinds `boot-test.py` (TERM, HUP), so its QEMU is killed when only the
  runner is signalled; the handler acts once (a second signal doesn't cut
  its cleanup short), and a signal ignored on entry (nohup's HUP) stays
  ignored by it, as before (I6 review; QEMU itself still ends on a HUP to
  the group, so such a mode fails, cleanly). `batchprof.IMG_RESERVE` (the batch planner's model of
  an ImgTest2 job) is still (1, 5): widening it moves the batch schedules,
  which is left to a later step. Proof, 2026-11-27: three sets (the current
  one and the two before it) of all seven platforms, each image booted
  serial and at its width on Chopin (Linux, 4), Cinqcent, Copland, Cage and
  Czerny (2): 52 mode runs, every per-check verdict and every manifest the
  same, no FAIL either way (flakes.json is empty, so its match rate stays
  0 of 0); 45 reservations, none over its host's ceiling, the largest
  scope peak 0.64 of its D + M. Linux's four modes took 46-48 s together
  (151-164 s one by one), the BSDs about half.

## 10. Speedup 7: proof cells on all six amd64 hosts

- **Fan-out.** After a round, `img-round.sh fanout` xcopies every platform
  that passed from its builder to every proof host: all six amd64 hosts by
  default (D12). It runs host to host and in parallel, and brings holt-bin/P
  and provenance.json along. img-xcopy already accepts any hosts.json name;
  it loses only its compression step (§7). Disk per host: about 15.3 GB for
  the current set and the same per kept `prev-*` set. Copland has 282 GB
  free. Before it copies, fan-out checks that DEST's `holt-bin/P` is the
  plan's or the image's, so that a fan-out never rolls back a newer holt-bin
  (C10 keeps promotions out meanwhile), and that DEST's bko-bin matches the
  image's `bko-bin.sha256`, which img-xcopy does not bring along (else the
  pin writes `bins-stale`).
- **Order.** ImgTest2 and the +img panel start only once fan-out has ended:
  a host whose copy is still under way has no `done.exit`, and its pin
  refuses the platform.
- **choubox.** Its img cells fit (`--reserve 1 5` within 3.0 6.0, at width
  1). Of the +img panel, only the lines with D ≤ 3 fit (dragonfly6,
  freebsd14, netbsd11; `ram-sizes.tsv`). The planner already knows this from
  `fit_ceilings`. If disk is short there, D12 lets it take the BSDs', illumos'
  and Haiku's test copies only. Linux always needs both images on a host,
  because its pin checks `release_same` against the release image.
- **Placement** needs no new rule. `plan.py place_img` and `place_img_panel`
  place a cell on any host whose facts show `images/P` with `done_exit` and
  no `bins_stale`. Once fanned out, every host qualifies, and the planner's
  existing ranking spreads the cells. Every +img line of the current panel
  (D 3-5, MEM 4) fits a small host's 9.0 10.1.
- Gain: ImgTest2's 7 cells and the panel's 7 lines spread over up to 6 hosts
  instead of 2. The panel's floor becomes its longest single line
  (dragonfly6+img, 16 gm) instead of the 26 min that two hosts took.

## 11. What a set costs, before and after (est.)

A binaries-only set, like both sets of 2026-11-26:

| phase | today (measured) | after I1-I12 (est.) |
|---|---|---|
| bko-holt builds | 9 min (VM; dragonfly longest) | 2-4 min (cross; haiku 7 min VM, beside it) |
| images | 37 min on Chopin, 3 lanes, full + xz | 6-8 min: 7 refreshes over 5 hosts, parallel boots, no xz |
| copy | 11 min to Cinqcent, xz on arrival | 3-5 min: raw, to 5 hosts in parallel |
| ImgTest2 | 19 min, 2 hosts | 3-5 min, 6 hosts, parallel modes |
| +img panel | 26 min, 2 hosts | about 17 min (one dragonfly6 line is the floor) |
| **active total** | **about 103 min** | **about 35 min** |

A full set (the OS-update cadence): longest single image without xz, on its
own host (illumos, about 15 min plus about 1 min of parallel boots), so about
17 min instead of 37, with the same copy and proof figures as above. Chopin's
share of image work drops from all of it to at most one unit in five.

## 12. Farm balance

The audit of 2026-11-26 (verified read-only that day) asked what else
favours Chopin and Cinqcent when it could use the whole farm. It found 20
findings, every one of which can spread fully or in part. Each is a step
below: what changes, what stays pinned and why, how the step is proven, and
the gain. Steps marked **(user)** need a decision in §13.2. F2 and F4 are
this spec's speedups 2 and 7.

### Large gains

- **F1. Guest updates on more hosts (partly) (user: FD1).** Today
  `guest.py:52-63` `first_host()` (the fleet role) runs every update, at most
  2 at a time on Chopin: GuestTest2's update round, vm-update and
  CarpetBeater's refresh. Change: any host that caches the base runs a
  guest's update in a `--keep` overlay, ships only the overlay to Chopin,
  and Chopin commits it and publishes it to vm-store. A per-guest lock
  replaces the single fleet lock for the update step.
  *Stays on Chopin:* the fleet lock for commit and publish, and the vm-store
  publish itself (D2: only Chopin talks to Bigfoot). *Needs:* the user to
  reword the rule "update on Chopin in a --keep overlay, ship only the
  overlay". *Proof:* one guest updated on copland and shipped; the committed
  overlay matches a Chopin update of the same guest (the same package list,
  GuestTest2 PASS). Then a full update round with per-guest locks and no
  double update. *Gain:* large: about 100 guests 2 at a time today. Over 5-6
  hosts est. a third to a quarter of the time, and the long DragonFly base
  rebuilds and installer updates move off Chopin's faulty cooler.
- **F2. Rescue images built on all build hosts (yes).** This is speedup 2:
  §5, steps I7 and I8.
- **F3. BackShot2 tier 1 and 2 of the large desktop sources (partly)
  (user: FD3).** `bs2:285 disk_ok()` admits SSD cases only on
  `holt_disk_mode` hosts, so about 14 sources with D over the small hosts'
  9.0 (`slim-sizes.tsv`: anduinos2, biglinux, endeavouros, mint22, mx25,
  neon24, omarchy, pop24, slackware15, solus49, vanilla3, zorin18 and
  others) land only on the big pair. Change: `disk_ok` follows
  `hosts.disk_scratch()` (overflow), with one SSD case at a time per host,
  and a source over a host's RAM ceiling uses that host's SSD. Repo-on-SSD
  alone brought copland's peaks under 9 on 2026-11-25 (anduinos2 5.5,
  biglinux 6.9 GiB). *Stays big-only:* cases whose memory exceeds a small
  host's 10.1. *Proof:* `bs2 plan` shows the sources on 5 hosts. Then one
  tier-1 case of each moved source PASSes on a small host, with its wear tag
  in jobs.log. *Gain:* large: BackShot2All's Linux lane over 5 hosts instead
  of 2.
- **F4. ImgTest2 and the +img panel on every host (yes).** This is speedup
  7: §10, step I9.
- **F5. About a dozen guests missing from the small caches (yes).** leap16,
  qubes43, slackware15 and -uefi, truenas25 and -mirror, uc24 and uc24-fde,
  void-zbm and -enc are on chopin and cinqcent, but none of them on copland,
  and only some on cage, czerny and choubox. Change: publish or relink them
  into the small caches (slackware was rebuilt on 2026-11-25 and may not be
  republished yet). Correct the stale "copland and cage have no OpenZFS"
  text in classes.json `needs_host_zfs_why`: every amd64 host runs ZFS 2.4.4.
  HoltTest lines with D 16-27 use the small hosts' SSD under overflow.
  *Stays big-only:* qubes43 (M 12 is over 10.1). uc24-fde needs swtpm on the
  host, a capability to check. *Proof:* `pick-host --guest G` lists the small
  hosts. One HoltTest2 line of truenas25 and one of slackware15 PASS on a
  small host. *Gain:* medium-large: about 11 guests gain 3-4 hosts, among
  them long lines (truenas D 25-27, slackware 16).

### Medium gains

- **F6. run-all.sh's name-keyed profiles and units (yes).**
  `compare/run-all.sh:194-200` keys profiles by host name. In `:256-265`,
  bcachefs has HOME chopin, ZFS units need big, and netbsd has HOME cinqcent
  and needs big too.
  Change: drop the netbsd HOME and its big requirement (netbsd11-rust.qcow2
  is on all 5 build hosts; check its peak against a small host's ceiling).
  Drop the ZFS "big" requirement once the loop pools are checked against a
  small host's 10 GiB RAM disk under JOBS_CAP 1. Make bcachefs's HOME "any
  host with the tool", or copy the arch-root bcachefs tool to the small
  hosts. Replace the profiles with capabilities from facts. *Proof:* a
  SPLIT=all round deals units to all 5 hosts, with the same verdicts as the
  last round. *Gain:* est. 10-15 min off a SPLIT=all round.
  *As built (2026-11-26):* the SPLIT deal goes by capability only. Its
  units name the capability words they need (`zfs`, `bcachefs`,
  `vm:GUEST` for a guest platform, a 6.5 GiB `ceil:` for a linux step,
  one fs_roundtrip.py's RAM peak) and netbsd prefers `netbsd-build`; no
  HOME, no class. The words come from the facts: `facts_host.py` caps gained
  `bcachefs` (a format tool and the kernel's file system) and
  `netbsd_build`, and `testvm/test2/split-caps.py` prints `SPLIT_CAPS`
  from a live probe, which every SPLIT host must be given alike (run-all.sh
  refuses SPLIT without it). The audit's premise on bcachefs was stale:
  every amd64 host has bcachefs-tools 1.39.7 and the DKMS module from the
  backports, so nothing was copied; the arch-root tool is still used where
  it is (chopin, cinqcent). The profiles were **not** replaced: they are
  each host's default share, the one GateTest2's `loop:linux` and
  `loop:sparse` cells run, and changing them would change those cells; only
  their stale comments were corrected. Choubox (ceiling 3.0) gets no linux
  step and, with five guest platforms and five sparse steps over six hosts,
  nothing at all, so the round uses the five build hosts. *Proof run
  2026-11-26* (bko-bin, OLD_BIN none, each host's share under one
  cmp-run.sh reservation, RT_RAM=1): chopin zfs-onto-ext4, btrfs,
  sparse zfs-nocomp, netbsd; cinqcent zfs, xfs, sparse zfs, freebsd;
  copland nonroot (its ZFS pool), f2fs, sparse ext4, openbsd_illumos; cage
  bcachefs, tmpfs, sparse xfs, dragonfly; czerny ext4, jfs, sparse btrfs,
  zfsguests. All 154 rows PASS, as every host's last GateTest2 loop rows
  (2026-11-24); the longest share took 637 s. RAM-disk peaks 12.4 GiB on
  the big hosts (two linux steps at once), 6.1 on the small ones. ZFS on a
  small host, checked apart on cage under JOBS_CAP 1 (zfs, zfs-onto-ext4,
  nonroot, sparse zfs and zfs-nocomp): 22 rows PASS, peak 7.4 GiB, under
  its 9.0 ceiling.
- **F7. GateTest2 fast layers pinned by name (partly) (user: FD6).**
  `gate/layers.json` pins gate:holt and gate:fork:restic (and fork-root) to
  chopin, and the rustic forks to cinqcent, because the stock baselines live
  only there. Change: run `stock-baseline.sh --host/--restic-host` once on an
  M900, then unpin, or let placement choose (four-host-split P19). Drop the
  gate:holt pin: its input `/bkoscratch/test2/h1-raw` exists on no host
  today. *Stays pinned:* fork-root, only if its root tests need a big host
  (to check). *Proof:* each unpinned layer PASSes on a small host with the
  same counts as its last pinned run. *Gain:* est. 5-15 min of fast-part
  wall time per GateTest2, with the heaviest compiles off Chopin.
  *As built:* no fast layer has a pin but gate:selftest (the Mac's), fork-root
  included: its reserve is 1 2 and the small hosts have their own
  mkfs.bcachefs. The plan keeps an unpinned layer off a host its arch or
  ceiling rules out, and off one whose facts say a host input marked
  `"need": true` is absent (`gate.needed_inputs`: each fork's stock
  `results.tsv` and the rustic toolchain), then places it with
  `gate.fast_place`: the shared host (the fleet role, Chopin) last, the three
  forks (one fork-tests.sh per host, run at once) on distinct hosts where it
  can, then the tie order. fork-root:restic has `"place_with":
  "fork:restic"`: its root-list need reads fork:restic's m run of the commit
  on its own host (elsewhere the need is SKIP, 30 PASS instead of 31: the
  first small-host run found it), so it goes where fork:restic is placed, or
  where its newest row ran. The code's reality differed from the text in two
  places, fixed with it: the forks' stock paths in layers.json still named
  restic 0.19.1, rustic_core 0.13.0 and rustic 0.11.4, absent on every host
  since the 2026-11-22 STOCK move, so the judge key never saw the baseline
  fork-tests.sh read (test_gate now checks them against audit-lib.sh); and
  fork-tests.sh's `--reserve 6 12` is over a small host's ceiling (MEM
  10.1), so the fork layers got `reserve` 6 12 and `host_reserve` small 6 10,
  which the plan checks and run_forks passes as RESERVE. The baselines were
  built off Chopin on 2026-11-26 (`stock-baseline.sh --restic-host cage
  --host copland`, RESERVE 6 10): restic on cage, rustic_core and rustic on
  copland, 7 min, the same counts as Chopin's and Cinqcent's (1728/0/33,
  363/0/0, 76/0/6) and byte-identical `results.tsv`, so a fork cell's key is
  the same wherever it runs. *Proof run 2026-11-26* (`GateTest2 holt
  fork:restic fork:rustic_core fork:rustic fork-root:restic --hosts
  copland,cage,czerny`, then fork:restic and fork-root again with
  place_with): every layer PASS on a small host with its last pinned run's
  counts. gate:holt on copland 985 passed, 17 filtered, 4 ignored, corpus 98
  (Chopin's 135 s, copland's 142 s); fork:restic on cage 1930/0/73, builds
  13; fork:rustic_core on copland 706/0/0, builds 3; fork:rustic on copland
  85/0/6 (builds 3, not 2: the one-time prep-rustic-src row of a tree new to
  that host); fork-root:restic on cage 31 PASS, root-list PASS, each fork job
  under `--reserve 6 10`. Choubox (ceiling 3.0) takes no fork layer.
- **F8. bko-holt and bko-rescue builds on big hosts only (yes).**
  `holt.py:255-263` `big_build_hosts` needs class_rank 0, and t2 batch
  refuses a HoltTest2 set without a big builder. Change: drop the class
  filter. `build-holt.sh --reserved` gains an SSD path under overflow, or a
  cap of one build on a small host. The one-builder-per-platform-then-copy
  flow stays, because builds are not reproducible. Speedup 5 makes most of
  this moot for the BSDs. *Proof:* a HoltTest2 run on an all-small set
  builds and PASSes, and `t2 batch plan` accepts such a set. *Gain:* medium:
  6-12 min builds go one deep over 5 hosts.
- **F9. SticTest checks whose reserve exceeds a small RAM disk (partly)
  (user: FD4).** debian13-zfs, ubuntu24 and ubuntu26-zfs need `13 5`,
  ubuntu26-zfsmirror `23 MEM`, qubes43 `16 4`. Change: give check.sh the
  `--reserve-ssd` overflow that HoltTest has (the small hosts have hundreds
  of GiB free on /bkoscratch). *Stays big-only:* qubes43 (and it is only in
  Chopin's vms/). *Proof:* each moved check PASSes on a small host with its
  scratch on SSD, and its wear is logged. *Gain:* five of the longest
  SticTest cells stop queueing for the big RAM disks.
- **F10. Audit and compare scripts default to Chopin (yes).** `rust-lint.sh:72`,
  `stock-baseline.sh:42`, `rustic-exact-round.sh` (a hard-coded Chopin IP,
  and a thermal wait for Chopin only) and `compress-images.sh:38`. Change:
  default to `pick-host` or a build-role host. Check that the rustup targets
  exist on the chosen host. Take the ssh target from hosts.json (`ip`,
  `host_key_alias`). Add a per-host thermal field instead of the Chopin-only
  Tctl wait. Spread lint targets over hosts. *Proof:* `rust-lint.sh` on a
  small host gives the same findings as Chopin's baseline. *Gain:* medium
  for RustAudit and ForkAudit rounds.
  *Built 2026-11-27:* the candidates are the table's amd64 build-role hosts
  (or `--host`/`--hosts`). `testvm/audit/audit-hosts.py` probes each over
  ssh (rustup's toolchains, targets and components, a Go, flock, time, cc;
  read only) and `audit-pick.sh` ranks them with `pick-host --reserve`.
  rust-lint.sh deals its targets (`audit-hosts.py lint-plan`, `--spread N`,
  default 3): the best host with 1.94.1's clippy and rustfmt and 1.85.1 is
  the primary (fmt, doc, holt's parser pass and floor check, `--tests`,
  rustic, its share of the targets), and up to N-1 more hosts run
  `rust-lint-host.sh --clippy-only` over the rest. Each target goes only to
  a host that has its std (the build-std ones: the nightly's clippy and
  rust-src). The outputs merge into one `raw/`. A host that lacks a part is
  named, with the `rustup` command that adds it. stock-baseline.sh puts
  restic on the best host with a Go and the Rust pair on the best other one
  with the toolchain; it still looks for a finished baseline on every build
  host first. rustic-exact-round.sh takes each host's ssh from the table: a
  row with an `ip` by it, under `HostKeyAlias`. Chopin's row now holds its
  `ip`, 192.168.68.194, which was the script's hard-coded address. Two new
  limits replace the name checks: `hot_max`, the Tctl wait before a job
  (Chopin 95, the rest none), and `xz_threads`, compress-images.sh's `-T`
  (Chopin 2, default 4). Only on that hunk does compress-images.sh change
  (see the note under §14). On 2026-11-27 only Chopin had every part. Cinqcent
  lacks rustfmt and the nightly's clippy. The small hosts had no clippy, so
  copland got 1.94.1's clippy, rustfmt and the freebsd, netbsd and illumos
  stds, and the nightly's clippy. Proof (holt c4917fc8, rustic_core 695d676,
  rustic 2c3b237, the RA2 baseline's commits): `--host chopin`
  (20261127T004905), `--host copland` (20261127T004907) and the default,
  spread over chopin, cinqcent and copland (20261127T004909). All three have
  the same summary table. Their 33 filtered outputs are equal except for the
  paths of their inputs, and `rust-filter.py --compare` finds 0 new either
  way. The committed `baseline.txt` (2026-11-20) differs only in rows the
  filter has gained since: walkdir, and rustic_core doc's upstream split.

### Small gains, hygiene and reliability

- **F11. HoltTest2 pins (yes).** In `classes.json:66-67`, the redox pin is
  stale (redoxfs is on all 6 hosts): drop it. openbsd8's input is on chopin,
  copland and cage, so replace the one-host pin with a file-presence
  capability like `has_uefi_rescuer`. Copy `dragonfly6-newfsid.qcow2`, then
  unpin dragonfly6. *Proof:* the plan places these lines on other hosts, and
  each PASSes once there.
  *Built 2026-11-26:* `holt.pins` is `{}`. `plan.py holt_prep` reads a BSD
  source's one-time rescuer preps from `holt/spec.json` (its `NAME.qcow2`
  rescuers, so the illumos sources' `omnios58-reguid.qcow2` too, which keeps
  them off choubox, the one host without it) and keys its cells only on the
  hosts whose facts list them; with none, anywhere, and the first run makes
  it (ht1-bsd.sh), as before. +img cells are exempt (the image is their
  rescuer). `dragonfly6-newfsid.qcow2` (sha256 3ad5f08e...) went from
  cinqcent to the other five hosts. `classes.json` is gate:selftest's harness,
  so that cell is due once. Proof: `t2 HoltTest2 openbsd8 dragonfly6 redox
  --hosts copland,cage,czerny` (t2h-20261126T2114-82fa): openbsd8 PASS on
  copland (czerny's runner left it to the others), dragonfly6 on cage, redox
  on czerny.
- **F12. New guest builds (partly).** Copy `/bkoscratch/rebuild-inputs` to
  the build host, and ship the finished image to Chopin for its vm-store
  publish. *Stays on Chopin:* the publish (D2). *Proof:* one make-*.sh build
  on copland, published from Chopin, then GuestTest2 PASS.
- **F13. Ties go to Chopin (yes) (user: FD2).** The planners break ties by
  table order, and Chopin is row 1 (`run-all.sh` split_share,
  `gate.py host_for`, `plan.py` estimate's packing and its gate placement,
  `img.py` target, drive.py's simulate (by name, so cage first), bs2's deals
  and leftovers host). Change: break ties by class, then by a heat or speed
  measure, then by order. Regenerate the golden fixtures that assume Chopin
  first, and check that nothing reads row 1 as the fleet host (fleet
  ownership comes from the role now, `guest.py:52-63`). *Proof:* t2's
  self-test with the new goldens, and a plan diff showing ties moved.
  *Built 2026-11-26:* `hosts.py tie_key` is (class rank, heat, row, name), the
  smallest winning; `heat` is a new limit (0 to 9, default 0), chopin 1 for
  its cooler (the temperature sampler's medians: chopin 40.4, cinqcent 35.7 C),
  so a tie between the big hosts goes to cinqcent and the small ones keep the
  table's order. `hosts.py names --ties` gives the order to the shell. The
  plan estimate's GuestTest2 update round read the first reachable host as the
  fleet host; it now takes `guest.first_host()` (the role). What the audit
  listed but is no tie, left as it is: `plan.py:1018/1063/1098/1176` set the
  host when one is left; `img.py:932` labels a row; promote-bins' default
  source is where the sets are assembled, not a tie, and moving it would
  promote another host's older set (F20 decides it); bs2's HOME deal and its
  `leftovers` default host follow where the sources are built.
- **F14. Warm holt dirs on Chopin (yes).** There are 82 tags in
  `/bkoscratch/holt-warm` on chopin and 0 on copland. Change: choose the host
  with `pick-host --reserve 2 4` and reword the fix-cycle-speedups note.
  Each new host's first build is cold. *Proof:* one fix cycle on copland.
  *Gain:* small per cycle, but the job is the most frequent one, and the
  warm dirs stop filling Chopin's 81 % full P3.
  *Built 2026-11-27:* `testvm/holt-warm test TAG [--host H] [-- ARGS]`
  (on the Mac) uses the host recorded for TAG
  (`~/backotter-work/holt-warm/TAG.host`), else the one `pick-host --reserve
  2 4` chooses, and records it, so a cycle's later runs stay warm. It rsyncs
  holt/ (no target/), `testvm/test2/classes.json` and
  `docs/design/holt-json.md` (the two files holt's tests read beside holt/)
  to `HOST:/bkoscratch/holt-warm/TAG/src`, and runs `cargo test --offline`
  there under the host's `cmp-run.sh --reserve 2 4` (WEAR_TAG
  `holt-warm:TAG`), with Rust `HOLT_RUST_TOOLCHAIN` (1.94.1, build-holt.sh's
  rule, not the host's rustup default, which differs: 1.94.1 on Chopin,
  1.85.1 on the others), the target dir kept beside src and TMPDIR in the
  RAM disk (`testvm/holt-warm-host.sh test`). The spec's "prune" half:
  `holt-warm prune [--host H]... [--days N] [--whole] [--dry-run]` removes,
  of each tag that nobody used (its `.last-used` stamp) or changed in N days
  (default 7) and that no process has open and no `.cargo-lock` holds, its
  cargo target dirs (cargo's CACHEDIR.TAG), the bulk of a tag; `--whole`
  removes the tag. On Chopin it pruned 42 of 82 tags' targets (57 GiB by
  du; 345 to 392 GiB free). *Proof:* tests `test_holt_warm.py` (24, on the
  Mac and on copland); a fix cycle on copland (a cold run, an edit, a warm
  run).
- **F15. HoltTest2's queue host is hosts[0] (partly).** `holt.py:658`. Change:
  choose the queue host by a role, or by the most reliable default host.
  `cmp-run.sh`'s QUEUE_HOST already lets the queue live anywhere. *Stays:*
  one queue host per run. *Gain:* reliability: a throttling or rebooting
  Chopin no longer stalls every host's pops.
- **F16. H1 dump collection on chopin and cinqcent (yes).**
  `h1/collect-dumps.sh:53`. Change: the default hosts come from hosts.json
  (the default amd64 hosts with HoltTest records), and the collector is an
  argument. *Gain:* coverage: the small hosts' dumps enter the corpus, and
  `--scan` checks each host's own strings.
- **F17. vm-store's default source names Chopin (partly) (user: FD5).**
  `vmstore.py:68` `DEFAULT_CHOPIN`. Change: read the store-mirror role. *Stays:*
  publish, push, verify and sculpt-update on Chopin (D2). A second mirror on
  Cinqcent waits for the user to revisit D2.
- **F18. BackShot2 leads, leftovers and the SSD lane (partly).** Change: let
  any host that holds all its sources lead, or publish the BS2 sources to
  vm-store. *Stays:* the leftovers chain on one host (it needs all three
  UEFI sources on one disk), and the SSD lane until the per-host test-disk
  plan (Dec 2026).
- **F19. BackShot1 QEMU rehearsals "on both hosts" (yes).** A docs and
  habit change: place them with `pick-host`, check the guest source is in
  the cache, and note that void's rootfs check needs minisign or signify on
  the host.
- **F20. Build-round collector and promote-bins source (partly).**
  `build-round.sh:56-66` and `promote-bins.sh:14-19`. Change: choose the
  collector by role or load. Relax promote-bins' big-class rule, since every
  build host ends a round with the whole bin, once images are dealt (§5.3).
  *Stays:* `--split legacy` and ROUND_BUILDERS, which exist to rebuild or
  read old rounds as they were built.

### What stays pinned, and why

| what | where | why it cannot move |
|---|---|---|
| vm-store publish, push and verify; sculpt-update | Chopin | D2: only Chopin talks to Bigfoot (bigfoot-link role) |
| The fleet lock and the commit of updated guests | Chopin | One committer per guest generation. F1 moves only the update work |
| qubes43's Stic and Holt cells | big hosts | M 12 GiB is over a small host's 10.1 |
| uc24-fde | hosts with swtpm | a host capability, not a placement habit |
| The BS2 leftovers chain | one host | needs all three UEFI sources on one disk |
| The BS2 SSD lane | holt_disk_mode hosts | waits for the per-host test-disk plan (Dec 2026) |
| One builder per platform, then copy | wherever it was built | builds are not reproducible, so a second build is not the same binary |
| `--split legacy`, ROUND_BUILDERS | chopin, cinqcent | exist to reproduce rounds built before 2026-11-14 |
| HoltTest2's single queue host | one host per run | one queue is the design; F15 only changes which host |

## 13. Decisions

### 13.1 The seven speedups

1. **D1. How a refresh swaps the binaries.** *Recommend:* Linux re-packs the
   layer from the parent's own layer tree (§4.1). The other six swap in
   place, in the parent's file system, inside the platform's VM
   (`build.sh refresh`, §4.2), with C2's tree diff on every refresh and the
   headroom rule in every full build. *Alternative:* split every
   `build.sh assemble` into a cached "stage" (media, unpacked and updated)
   and a "pack" step, and refresh by re-packing. That reuses the build's own
   pack code, but means rewriting six build scripts and keeping a 1-2 GB
   stage tree per platform per host. The in-place swap is what the user
   proposed, touches fewer tested bytes, and C2 proves it each time.
2. **D2. A refreshed image's identity.** *Recommend:* a new `image_id`, with
   the labels derived from it rewritten (§1.3's id sites), and the random ids
   a full build makes drawn anew: OpenBSD's DUID (required: its fstab mounts
   root by it, so a parent and child stick plugged in together could mount
   each other's root) and the GPT GUIDs on NetBSD and illumos. Two different
   images must never share an id or a file-system label: two sticks plugged
   in at once would collide. *Alternative:* keep the parent's id and add a
   `refresh` counter to the marker. That needs fewer edits, but one id would
   name two different images.
3. **D3. The OS-update cadence.** *Recommend:* a platform gets a full build
   when its base digest changes, when its last full build is 14 days old,
   or when the user asks. CarpetBeater's refresh step builds a full set. The
   other rebuilds are refreshes.
4. **D4. Which hosts build images.** *Recommend:* the five build-role hosts
   (chopin, cinqcent, copland, cage, czerny), one image at a time on a small
   host and two on a big one, with Chopin penalised. Not choubox (no build
   role, a 4G RAM disk).
5. **D5. Seeding Linux's inputs.** *Recommend:* copy the current ISO and slim
   (about 5 GB) to copland, cage and czerny with `img-seed.sh`, and repoint
   all of them whenever slim is repointed. A host whose slim differs gets no
   Linux unit.
6. **D6. Retention without compression.** *Recommend:* keep the current
   image and the last two `prev-*` sets raw on every host. With the user's
   yes, delete older `prev-*` and `prev-x*` directories (on Chopin today that
   is most of `images/`'s about 71 GB). It is a permanent deletion, so it is the
   user's call, and nothing is deleted before they say so. The user also
   says whether that yes is a standing rule (each round deletes what falls
   out of the last two, by itself) or one cleanup now, with each later one
   asked again. I recommend the standing rule, scoped to `images/P/prev-*`
   and `prev-x*` only: without it, every round adds 15.3 GB per host.
   Published sets stay as `.xz`.
7. **D7. What "publish" is.** *Recommend:* `img-publish.sh SET`, started by
   the user or by CarpetBeater, on a host that is not Chopin, writing
   `images/publish/SET/` with xz and both checksums. Nothing is published
   automatically after farm rebuilds.
8. **D8. Boot-test parallelism.** *Recommend:* run as many modes as the
   reservation holds at 4.5 GiB each (4 on a big host, 2 on a small one, 1
   on choubox), and keep the guests at 4 GiB so that the test conditions do
   not change.
9. **D9. Cross-compile scope.** *Recommend:* FreeBSD, NetBSD and illumos
   first (tier 2, rustup's std), then OpenBSD and DragonFly (tier 3, a
   nightly with `-Zbuild-std`). Haiku stays a VM build (no usable cross
   toolchain without building Haiku's own). bko-rescue follows the same
   path when it ships.
10. **D10. The cross toolchain.** *Recommend:* pinned 1.94.1 for tier 2,
    the same toolchain the Linux bko-holt uses, and a pinned nightly for
    tier 3. This changes the compiler that ships the BSD binaries from each
    guest's packaged Rust (1.98-1.100 today) to a pinned one, and stops it
    changing when a guest updates.
11. **D11. When a cross build may ship.** *Recommend:* after E2 (§8.2) passes
    on two consecutive holt-change rounds, platform by platform. The VM
    build then runs as a reference weekly and on every toolchain or guest
    change. For OpenBSD, E2's step 0 must show the same IBT handling as the
    VM build (§8.2), because no farm CPU enforces IBT. If it does not, or if
    the user wants proof on hardware, OpenBSD stays a VM build until a run
    on an IBT-capable Intel machine passes.
12. **D12. Fan-out scope.** *Recommend:* every amd64 host gets the release
    and test images of the current set (Linux's pin needs the release
    image for `release_same`). If choubox's disk is short, it takes only the
    test copies of the six non-Linux platforms, and Linux's both.
13. **D13. The in-build boot tests.** *Recommend:* keep them in `img-one.sh`
    as the gate before fan-out. They become cheap with parallel modes, and
    a bad image is caught before it is copied to five hosts. Whether
    ImgTest2 can then skip modes the build already passed is Open 2.
14. **D14. Build order.** *Recommend:* I1-I2 first (provenance and the
    planner; no behaviour change), then I5-I6 (no xz, parallel boots: quick
    and independent), I3 (Linux refresh), I7-I9 (spread), I4 (BSD refreshes,
    one platform at a time, NetBSD first), and I10-I12 (cross) last.
15. **D15. Host tools in the base digest.** *Recommend:* yes, for the
    packages that write image bytes on the host (§3.2: dosfstools and mtools
    everywhere, xorriso and squashfs-tools for Linux). A host whose versions
    differ from the plan's then gets no unit of the platforms they touch, and
    a package update on the farm forces one full build of those platforms.
    *Alternative:* record them in provenance only. Builds stay possible on
    every host, but two hosts could make different bytes from "the same"
    inputs, and I7's equivalence proof would hold only for the versions it
    ran on.
16. **D16. Promotions during an image round.** *Recommend:* C10's round
    lock. `promote-bins.sh` and `img-refresh-holt.sh` refuse a host while a
    round holds it (they say which round, and the user waits or stops it),
    and every unit refuses binaries that are not the plan's. This changes
    promote-bins, which the user runs by hand, so it is the user's call.
    *Alternative:* no lock, and the round's summary flags a set that mixes
    binaries. That is cheaper, but a mixed set is then caught only after it
    was built and fanned out.
17. **D17. Where the cross build's key lives.** *Recommend:* §8.2a, a
    build-kind input in HoltTest2's holt key and its own `cross/`
    directory under CODE, so that the switch to a cross build makes every
    holt line of that platform due once. *Alternative:* fold the build kind
    into CODE itself. That is simpler, but CODE would then no longer name
    the code alone, and every tool that reads CODE as "the code" would need
    checking.

### 13.2 Farm balance

1. **FD1. Guest updates off Chopin (F1).** Reword the rule "update guests on
   Chopin in a --keep overlay, ship only the overlay" to "update on any host
   that caches the base, in a --keep overlay; ship only the overlay to
   Chopin, which commits and publishes". *Recommend yes*, after one guest's
   side-by-side proof.
2. **FD2. Ties (F13).** *Recommend:* a tie-break rule (class, then heat or
   speed, then table order) rather than reordering hosts.json, which other
   tools and notes read.
3. **FD3. BackShot2 SSD cases on small hosts (F3).** This puts more NVMe
   wear on the small hosts' SSDs. *Recommend yes*, one SSD case at a time
   per host, with wear tags.
4. **FD4. SticTest `--reserve-ssd` overflow (F9).** The same wear trade.
   *Recommend yes.*
5. **FD5. A second vm-store mirror (F17).** *Recommend:* read the role now.
   Leave the second mirror until the user revisits D2.
6. **FD6. GateTest2 pins (F7).** *Recommend:* build the stock baselines on
   an M900, let placement choose, and drop the gate:holt pin.

## 14. Steps

Each step is done when its proof passes. Figures are rough (est.).

| id | what | proof (done when) |
|---|---|---|
| **I1** | `stick/img-inputs.tsv`, `img-digest.sh`, the tsv lint (§3.2). `img-one.sh` writes `provenance.json` (full builds) with the tree listing (`os_manifest`), the per-platform swap and id-site lists checked against §1.3, the FreeBSD builder's fingerprint, and the build-time identity recheck and guest lock (C7). (~1 d) | Unit tests in a fixture world (BKO_IMG_DIR and a test T2_HOST_ROOT): digests stable across two runs, and each input class moves only its own digest. Two hosts with the same inputs give the same base digest. The lint fails on a new unlisted stick/ file. Two FreeBSD builds on one host with no builder change give the same base digest. A vm-update of a builder guest during a fixture build makes the build refuse. A full set built with I1 matches its pre-I1 counterpart in every image file but the provenance |
| **I2** | `img-round.sh plan` (§3.3, §5.2 steps 1-3), with no execution. Image units in `deal-units.py`. (~1 d) | Table tests over fixture facts: every row of §3.3, a host with a mismatched holt-bin refused, a stale slim refused. On the farm, `plan` right after a fresh set says reuse ×7 |
| **I3** | Linux refresh: `build-backotter-layer.sh --refresh-from`, `img-one.sh --refresh-from`, C2 and C3 checks. (~1 d) | **E1**: on one day, two full builds with the same bins (A, A') give the noise floor; then, with new bins, a full build B and a refresh R from A. diff(B, R) must fall within diff(A, A'): ids, times, nothing else. A, A' and B run within one hour, and a package list (manifest) that differs between them voids the trial, since -updates may have moved. R passes all 4 boot modes, then ImgTest2 and the +img panel. A deliberate extra change in the layer makes C2 refuse |
| **I4** | `build.sh refresh` for netbsd, then freebsd, openbsd, dragonfly, illumos, haiku. The headroom rule in their full builds, where §4.2's measure says it is short. (~0.5-1 d each) | E1 per platform. fsck clean before and after. The id sites rewritten (the label checked from the booted image), OpenBSD's DUID and the GPT GUIDs new. With parent and child attached to one guest, each boots its own root. The marker's kernel and ZFS fields equal the parent's. A too-large binary set makes the refresh refuse and the planner fall back to a full build |
| **I5** | `BKO_IMG_COMPRESS=0` in img-one.sh and img-xcopy.sh, `img-one.sh` writing `SHA256SUMS`, and `img-publish.sh`. The retention rule only after D6. (~0.5 d) | A set builds with no xz process (sampled ps and wear tags). Facts give the same release SHA-256 as before, and ImgTest2's keys for an unchanged image are unchanged. Publish's decompress-and-hash matches the raw SHA-256 of every image, and publish refuses a set past the cadence |
| **I6** | Parallel modes in img-one.sh and ImgTest2's boot.sh, the image copied into the RAM disk, reservations sized by width. (~0.5 d) | The same image tested serial and parallel gives the same per-check verdicts on 3 sets. No reservation goes over its ceiling (cmp-run logs). The flakes.json match rate is unchanged |
| **I7** | `img-seed.sh`, and Linux inputs on copland, cage and czerny. (~0.5 d) | The slim manifests match. A full Linux build on copland and one on Chopin from the same inputs, the same day, have the same layer manifest (package list), the same base squashfs bytes, and both PASS ImgTest2 |
| **I8** | `img-round.sh run` and `fanout`, the round lock (C10, after D16). Retire Chopin's `images/lanes.sh`. promote-bins' source relaxed (F20). (~1.5 d) | A full farm round: each platform built where `plan` said, identical SHA-256 on every holding host, every host's pin accepts every platform, and no host over its limits. A promote-bins started mid-round is refused, and a unit started with a host's sets changed under it refuses |
| **I9** | Proof cells on six hosts: fan-out to all, choubox's width-1 boot cells. (~0.5 d) | An ImgTest2 run places cells on at least 5 hosts. The panel's lines spread, and its wall time drops to about its longest line |
| **I10** | `holt-cross.sh` for freebsd, netbsd and illumos, as HoltTest2 check builds in `CODE/cross/` with their own key (§8.2a, D17). (~1.5 d) | E2 steps 0-3 for each. A check build never lands in `CODE/PLAT/` |
| **I11** | The same for openbsd and dragonfly (tier 3). (~1.5 d) | E2 steps 0-3 for each |
| **I12** | The switch per platform after D11, the weekly VM reference, `holt_build` in the provenance. (~0.5 d) | E2 on two consecutive rounds. At the switch, `t2 plan` shows every holt line of the platform due. The first image set built from cross builds passes ImgTest2 and the panel |
| **F1-F20** | §12, each with its own proof. | As listed in §12 |

Shared files the work touches, each a small hunk: `img-one.sh`,
`img-lanes.sh`, `img-xcopy.sh`, `build-backotter-layer.sh`,
`make-img-bsd.sh`, every `stick/P/build.sh` (one subcommand each),
`deal-units.py` (image units), `promote-bins.sh` (the source rule and the
round lock), `img-refresh-holt.sh` (the round lock), `testvm/vm-update` (the
refusal's wording), `t2lib/img.py` and `img/boot.sh` (width), and
`t2lib/holt.py` and `t2lib/keys.py` (cross builds and their key).
`compress-images.sh` is unchanged; only its callers change (but for F10's
`-T` default, from the host table's `xz_threads`).

## 15. Open

1. **The test copy is a second full build.** On the BSDs the test image is
   assembled again with `TEST=1`: 18 of the 43 build-minutes, and it is not
   byte-for-byte the release image. Deriving it from the release by editing
   only the menu would save that time and make the tested image closer to
   the shipped one. That is not part of the seven, so it is left for the
   user.
2. **The boot tests run twice**, in img-one.sh and in ImgTest2. Once I6 makes
   them cheap, ImgTest2 could reuse the build's results for the same
   SHA-256 and harness digest. I recommend against it for now: ImgTest2 is
   the judged test.
3. **The arm64 Linux image.** Its refresh needs `holt-api.json` from an arm64
   bko-holt: run it on Bigfoot or under qemu-user, or keep the arm64 image
   on full builds.
4. **Haiku cross builds.** If HaikuPorts' cross toolchain becomes usable from
   Linux, Haiku can join speedup 5. Until then it stays a VM build, about
   7 min beside the others' 1-3 min.
5. **The estimates in §11** come from one day's two sets. The first rounds of
   I8 and I9 should record actual wall times per phase in `RUN/plan.json`
   against the plan's, as `t2 batch` records them.
6. **Bringing the FreeBSD builders alike.** Each host's freebsd15-builder
   is its own folder (§3.2), so FreeBSD units go only where the fingerprint
   matches the plan's. Publishing the builder to vm-store, or rebuilding it
   from `builder-setup.sh` on each host, would let FreeBSD spread like the
   rest. That is not part of the seven.

## 16. Review of 2026-11-26

Checked against `stick/img-one.sh`, `img-lanes.sh`, `img-xcopy.sh`,
`compress-images.sh`, `make-img.sh`, `make-img-bsd.sh`,
`build-backotter-layer.sh`, `marker.py`, every `stick/P/build.sh` and
`platform.conf`, `testvm/promote-bins.sh`, `vm-update`, `hosts.json`, and
t2's `facts_host.py`, `keys.py`, `tool_img.py`, `holt.py` and `img/boot.sh`.
What changed:

- **§7, I5**: `images/P/SHA256SUMS`, which only xz's script writes, is
  where ImgTest2's `release-image` key comes from. `img-one.sh` now writes it.
- **§8.2a, D17**: HoltTest2 keys bko-holt by its code, and never writes a
  binary over another in `CODE/`, so a cross build would have shipped on
  the VM build's PASSes. It gets its own key and directory.
- **§3.2, C7**: the FreeBSD builder is a per-host folder, written at every
  boot, so a qcow2 hash would have blocked every FreeBSD refresh. It gets a
  fingerprint. The plan's reference inputs are named. A build-time
  recheck and the guest's vm-update lock close the race with vm-update and
  vm-store.
- **C10, D16**: a round lock against promotions between units.
- **§1.3, §4.2, D2**: the id sites were incomplete: FreeBSD's second
  (hard-linked) fstab, DragonFly's fstab and loader.conf, OpenBSD's DUID
  (which fstab mounts by), and GPT GUIDs. The marker's kernel and ZFS fields
  are now carried over from the parent, since `build.sh info` reaches the
  network on FreeBSD.
- **§4.2 headroom**: DragonFly and Haiku already keep 256 MiB free. The
  short ones are FreeBSD, NetBSD, OpenBSD and illumos.
- **§1.1, §1.5, §3.2, D15**: the Linux layer also pulls from
  apt.bcachefs.org. The input list now names `img-one.sh`,
  `config-partition/`, `stick/layer/`, `stick/keys/`, the Linux scripts and
  the host tools, and a lint catches unlisted stick/ files.
- **§4.1**: holt-api is written in the chroot with today's feature checks,
  not on the host.
- **§8.2 step 0, D11**: an ELF-shape comparison, the only guard for
  OpenBSD's IBT enforcement, which no farm CPU exercises.
- **§9**: Linux's 1.0 GiB lane reservation cannot hold the image copied
  into RAM, and a failing first mode would have left no manifest.
- **§10, D12**: Linux cannot take test copies only. Fan-out checks DEST's
  holt-bin and bko-bin first, and the proof cells wait for fan-out to end.
- **§6, D6, publish**: a retest follows C9's lock protocol. D6 asks
  whether its yes is a standing rule. Publish refuses a set past the
  cadence.
