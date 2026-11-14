# OpenIndiana support

2026-11-14. Asked for by the user: support OpenIndiana Hipster in bko-holt and
in both archivers (restic-bko and rustic-bko), as the second illumos
distribution after OmniOS. The user approved this spec on 2026-11-14 with
every recommended decision (the last section).

**Conventions.** est. marks an estimate. *Check* marks a fact the sources
don't settle, to be settled on the first guest (O1) before the code that
depends on it. OpenIndiana's facts so far come from news of the 2026.10
snapshot (Phoronix and LWN, 7 Nov 2026), the openindiana-discuss archive
(the 2025.10 announcement) and the oi-docs handbook. The release notes and
the ISO itself have not been read yet. Nothing has been downloaded. Code
references are to local `main` at 1edb4b5 plus the uncommitted working tree.

## Summary

- OpenIndiana Hipster becomes a supported system for bko-holt and both
  archivers. It gets two QEMU guests (`oi2610` with UEFI, `oi2610-bios`), a
  SticTest1 entry, a place in **HoltTest1I** and the Test2 registrations.
- It is the same family as OmniOS (`Family::Illumos`): the same kernel
  lineage (illumos-gate master), the same loader, ZFS root pool, beadm,
  installboot, bootadm and IPS. Most of `holt/src/other/illumos.rs` and the
  illumos arms of load already apply. What is new is mostly *around* bko:
  - how the guest is built: an installer ISO, not a cloud image;
  - UEFI on illumos, which no guest covers today (`omnios58` is BIOS);
  - a restored system whose OS is newer than the rescue system's: an
    OmniOS r151058 rescue image restoring an OI root;
  - boot environments that pile up with each update;
  - the installer's dump volume.
- **No new rescue image** ([[one-kernel-rescue]]): the illumos image (OmniOS
  r151058) restores OI. The ZFS features match: gate master, illumos-joyent
  and r151058 have byte-identical `zfeature_common.c`. `load/plan.rs`'s new
  `zfs_problems` check catches it if that ever changes.
- **No new binaries, if they run** (Decision 4): the illumos restic-bko
  (Go, static) and rustic-bko (built on `omnios58`) should run on OI. illumos
  keeps its ABI stable from older releases to newer ones, and OI is the
  newer one (*check* `ldd rustic-bko` on the guest).
- About 3.5 d (est.), none of it the user's time beyond the OKs.

## What OpenIndiana is (as far as bko cares)

| | OpenIndiana Hipster | OmniOS r151058 (the precedent) |
|---|---|---|
| Release | Rolling (Hipster), built from illumos-gate master. Snapshot ISOs come out twice a year; the newest is **2026.10** (out by 7 Nov 2026) | Releases every six months, plus LTS; weekly updates within a release |
| os-release | *check*. Expected: `ID=openindiana`, `NAME="OpenIndiana Hipster"`, with VERSION_ID the snapshot (`2026.10`) or absent | `ID=omnios`, `VERSION_ID=r151058y` |
| Media | Three ISOs and USB images: **text installer** (servers, ~1 GB), **GUI live** (MATE, with the graphical installer) and **minimal**. SHA-256 sums are published beside them (*check* that they are signed) | A cloud image (`cloud.raw.zst`) with cloud-init; no installer used |
| Installer | Caiman's `text-install` (curses) on the text ISO; `gui-install` on the live ISO. As far as we know, neither takes an answers file (*check*: whether OI still ships the Automated Installer) | None: the image is already installed |
| Default layout | The whole disk, GPT, made by `zpool create -B`: an ESP (the bootsize partition), the `rpool` slice and the reserved slice 8. Datasets `rpool/ROOT/openindiana` (the BE), `rpool/export`, `rpool/export/home`, `rpool/export/home/USER`, and **`rpool/swap` and `rpool/dump`** zvols (*check* the sizes and names) | The cloud image's own GPT; `rpool/ROOT/omnios-r151058y` with `mountpoint=legacy`; no dump zvol, and the swap zvol our `user-data` adds |
| Firmware | BIOS and UEFI. 2026.10's live image gains UEFI framebuffer support. No Secure Boot (the loader isn't signed) | BIOS in our guest (`-machine pc`) |
| Loader | illumos' loader (FreeBSD's, ported; GRUB is gone since 2017). BIOS: pmbr, then `gptzfsboot` in the boot area. UEFI: `loader64.efi` as `EFI/BOOT/BOOTX64.EFI` on the ESP, with **no NVRAM entry** (illumos has no efibootmgr; *check*). Menu in `/rpool/boot/menu.lst`, made by `bootadm`/`beadm` | The same loader, BIOS only in our guest |
| Boot archive | `bootadm update-archive` (`/platform/i86pc/amd64/boot_archive`) | The same |
| Packages | IPS, `pkg update`, publisher `openindiana.org` (pkg.openindiana.org/hipster). An update that touches the kernel or other boot pieces makes a **new BE** and activates it for the next boot | IPS, the same behaviour |
| Init | SMF | SMF |
| NIC | vioif needs the legacy virtio NIC, as on OmniOS (*check*) | `--legacy-virtio-net` |
| Users | The installer creates a user and a root password, and root is a role on the GUI ISO (*check* on the text ISO) | root with a key, from cloud-init |
| GNU userland | `/usr/gnu/bin` (sed -i, find) | The same, plus `/opt/ooce/bin` |

## What already works, and what doesn't

**bko-holt `dump`:**

- `osrel.rs` `SUPPORTED` has no row: dump refuses OI. It needs
  `("openindiana", "*", "OpenIndiana")` (Decision 5), with a test that OI
  is accepted and that another `ID` on SunOS is still refused.
- `Family::of` takes `illumos`/`solaris` from `uname`: no change.
- `illumos.rs` reads diskinfo, the EFI label, `/etc/mnttab`, ZFS, `beadm
  list -H` and `bootadm list-menu`. Firmware comes from `prtconf -v`'s
  `efi-systab`: *check* that it reads `uefi` on the UEFI guest. That path
  has never been used, because omnios58 is BIOS.
- `esp_image` keeps an ESP that nothing mounts byte for byte (illumos
  doesn't mount its ESP): that is how the UEFI loader comes back. *Check*
  that OI's ESP is unmounted in `/etc/mnttab` and is FAT. The judge already
  ignores `bootsize`.
- The comment in `illumos.rs` names OmniOS only: it becomes "illumos
  (OmniOS, OpenIndiana)".

**bko-holt `load`** (the illumos arms in `commands.rs` and `other/load.rs`):

- The pool: `zpool create` with the source's features, `-t` under another
  name while the rescue image's own rpool is imported, and a note that the
  GUID can't be kept. All of this applies as it is.
- The datasets: OI's BE has a real mountpoint (`/`, `canmount=noauto`), not
  `legacy` as the cloud image's has, so it takes `zfs mount -O` through the
  bootfs rule (`commands.rs:2305`), not the `mount -F zfs` fallback.
  *Check* on the golden plan.
- **The dump zvol** (new): `rpool/dump` is created as a volume of the same
  size. Its contents aren't wanted (it is a crash-dump device).
  `/etc/dumpadm.conf` comes back with `/etc`, and the first boot's
  `dumpadm` takes it. *Check* that the archivers skip a dump zvol's
  contents as they skip swap's, and that load writes nothing into it.
- **Inactive boot environments:** the precedent stands. A clone comes back
  as a plain dataset whose origin snapshot isn't in the backup (judge.py's
  `_illumos.` and `origin` rules). Load mounts only the bootfs BE and the
  datasets that were mounted. An inactive BE (`canmount=noauto`, not
  mounted) is created but not filled, so after the restore `beadm list`
  shows it while it is empty (*check*: whether the archivers back up
  unmounted BEs at all; the dump records them). Decision 3 says what to do.
- **Boot** (`other/load.rs` `illumos_boot`):
  - `bootadm update-archive -R /target` runs on the rescue system, which is
    OmniOS, against an OI root. bootadm runs the target's own
    `boot/solaris/bin/create_ramdisk` with the rescue system's cpio and
    gzip. *Check* that the archive boots, and how judge.py's
    `EXTRA_BOOT_RE` treats the rewritten archive.
  - BIOS: `installboot -f -b /target/boot` writes the *restored* system's
    `gptzfsboot`, using the rescue system's `installboot`. `-f` overrides
    the version check. *Check* that OI's newer stage boots.
  - UEFI: nothing to install. The ESP came back with the table, and the
    firmware boots `EFI/BOOT/BOOTX64.EFI`. bko-holt makes no NVRAM entry
    on illumos, and none is needed (*check* that the installer made none).
- `zpool set bootfs=` and the menu: `/rpool/boot/menu.lst` is on the
  pool's top dataset and comes back with it.

**`redact.rs`:** `openindiana` is already in `SYSTEMS`
(`holt/src/redact.rs:139`). `openindiana` joins `GENERIC_HOSTS` if it is
the installer's default host name (*check*). The BE name `openindiana`
needs nothing: dataset names aren't redacted.

**The rescue image:** no change. The image's README and
`docs/design/zfs-images.md` add a line saying that it restores OpenIndiana
too, and why (the matching feature tables). `make-img-bsd.sh illumos`
stays OmniOS-based.

## Guests

**Amended 2026-11-14, after approval.** OpenIndiana's server also has a
supplemental **cloud image** (`20261107/OI-hipster-cloudimage-20261107.img.zst`,
926 MB, 8 GiB raw, BIOS and UEFI, built by oi-packer with OI's own small
cloud-config agent, not Python's cloud-init, SHA-256 only). The user chose
**both** routes: `oi2610` from the text installer (UEFI), and `oi2610-bios`
from the cloud image (BIOS), as `make-omnios.sh` does. That way one guest
has the installer's layout and the other oi-packer's.

Both guests get a 16 GiB disk and 2-3 GiB of RAM (*check* the installer's
minimum), and use `vm-run --legacy-virtio-net`. The inputs go in
`/bkoscratch/rebuild-inputs/oi2610/` (the text ISO, SHA-256 `c5cd9572...`)
and `oi2610-bios/` (the cloud image, SHA-256 `cd56ca63...`, with its
README and `build-info.json`) on every host that builds, with the SHA-256
pinned in the scripts.

| Guest | Firmware | Built from | What it covers |
|---|---|---|---|
| `oi2610` | UEFI (OVMF, Secure Boot off) | `OI-hipster-text-20261030.iso` and its text installer | The default install. The first illumos UEFI round trip: the ESP kept byte for byte and booted from the fallback path. The dump zvol. The OI root on the OmniOS rescue system. After one `pkg update`, two BEs |
| `oi2610-bios` | BIOS (`-machine pc`) | `OI-hipster-cloudimage-20261107.img.zst` and a NoCloud `cidata` CD | OI's own `gptzfsboot` put back by OmniOS's `installboot`, and oi-packer's layout (*check*) |

A mirrored rpool isn't proposed: `omnios58-mirror` already covers it, and
it is the same code.

How they are built (Decision 2):

1. **The text installer, driven over the serial console** (recommended): a
   new `testvm/make-openindiana.sh [--bios] SNAPSHOT WORK_DIR` boots the
   ISO under QEMU with the console on ttya. It picks the serial console
   from the loader menu (*check* its key, or set `console=ttya` in the
   loader). It answers `text-install`'s screens with a small expect-style
   driver: whole disk, default layout, time zone UTC, the user `tester`,
   root's generated password, host name `oi2610`. Driver: `console-boot.py`
   grown a script mode, or a new `testvm/openindiana/install.py`. Then the
   first boot runs a post-install over the console: root's SSH key, sshd
   allowing root by key, `/etc/default/login` CONSOLE left as is, and
   BACKOTTER-INSTALL-OK, as `make-omnios.sh` checks its marker. This gives
   the installer's real layout, BE name, swap and dump zvols and loader
   setup.
2. **By hand from the ISO's shell** (the fallback, if the curses screens
   won't drive reliably): from the live system's shell, `zpool create -B
   rpool`, the datasets, `pkg image-create` and `pkg install` of the ISO's
   package set, `bootadm install-bootloader`, `beadm`. This copies what the
   installer does, so it drifts from it: +0.5 d.
3. **One GUI install, kept as a pinned image:** not rebuildable by
   `rebuild-one.sh`, so not recommended.

Each guest gets:

- `rebuild-one.sh` lines (`oi2610)` and `oi2610-bios)`, the `asm` form, and
  the BSD/illumos power-off at :205);
- rebuild inputs on Chopin and Cinqcent, published to both by `vm-store`;
- `holttest1d/ram-sizes.tsv` rows (est. D 3, MEM 4, as omnios58's ratio;
  measured in O5);
- a GuestTest1 grade.

As for OmniOS, only Chopin and Cinqcent run them. The cage and copland
question doesn't arise: illumos pools are read inside their own rescuers,
not on the host (`classes.json`'s `needs_host_zfs_why`).

**vm-update:** `in-guest.sh`'s `SunOS)` section (`pkg update -v`, where 4
means nothing to do) works as it is. Two additions:

- **BE pruning:** keep the newest two BEs and `beadm destroy -F` the older
  inactive ones before updating. Without it, every weekly update leaves
  another clone, and the menu, `beadm list` and the dump grow with them.
  OmniOS needs the same and gets it too; *check* omnios58's count today.
- **The first update after install** may update `pkg` itself first
  (`pkg update pkg:/package/pkg`, then the rest), as Hipster's notes
  sometimes ask (*check* on O1's `--try`).

`fingerprint.py` already has `illumos` in `BASE_KINDS`, and `pkgset.sh`
names `base pm illumos`. GuestTest2's `L-ill` and `K-ill` rules (beadm's
`N`/`R` flags) apply as they are. `guest/known-lines.json` gains OI's
publisher line.

## Tests

**SticTest1** (`testvm/stictest1/oi2610`): `check.sh` and `extra.sh` from
`omnios58`'s, which are written for illumos and not for OmniOS (system
attributes, NFSv4 ACLs, named attributes, SIDs, tmpfs). They differ only in
the guest's paths: no `/opt/ooce`, and OI's own GNU tools under
`/usr/gnu/bin` (*check* that `testvm/compare/illumos/roundtrip.sh` needs
nothing from ooce). Both archivers and both hosts; 0 FAIL. `oi2610-bios`
gets no SticTest1 entry, because the archivers don't care about firmware
(Decision 6). `stic/manifests.json` (`"os": "omnios"`, so the illumos
binaries; see below), `limits.json` and `check-manifests.py`'s count go up
by one. Whoever lands last among the sibling sessions sets the number.

**HoltTest1:** both guests join **1I** (Decision 7):

- the README's 1I row becomes `omnios58`, `omnios58-mirror`, `oi2610`,
  `oi2610-bios`, and HoltTest1All's members follow;
- `ht1-bsd.sh` gets an `oi*)` case like `omnios*)`: `PLAT=illumos`, the
  same rescuer `RG=omnios58` (the OmniOS rescuer restoring OI, as the image
  does), `GPATH` without `/opt/ooce/bin`, and the same `BOOTSTATE` and
  `EXTRA_BOOT_RE`;
- the `omnios58-reguid` rescuer is reused as it is. Its rpool has a GUID of
  its own; OI's pool is a different pool, but its name is also `rpool`, so
  the `ht1src` alias still applies;
- `oi2610` boots the restored disk alone under OVMF with blank vars, which
  is the test that the fallback loader came back;
- judge.py's illumos rules apply as they are, plus whatever O5 finds about
  the dump zvol and the boot archive;
- `+img` (ImgTest2) uses the illumos image. `oi2610` joins
  `img_panel.illumos`, the first UEFI illumos cell there.

1BSD is not touched. illumos left 1BSD for 1I on 2026-11-05, so the
memory index's "1BSD BSDs+illumos" is stale and gets corrected in O7.

**Test2 registration** (in the sibling sessions' one batched pass, as
PikaOS's and the others' are):

- `classes.json`:
  - `stic.alone` and `stic.non_linux` gain `oi2610`;
  - `holt.groups.I` gains both guests;
  - `rustic_panel.illumos` stays `omnios58`;
  - `img_panel.illumos.guests` gains `oi2610`;
  - the `omnios58-reguid` rescuer entry's `guests` gains both;
  - no new canary: `omnios58` is already the "two boots" canary.
- `holt/spec.json`: `{"kind": "bsd", "plat": "illumos", "rescuer":
  ["omnios58-reguid.qcow2"]}` for each.
- `guest/spec.json`: inputs `testvm/make-openindiana.sh` and
  `testvm/openindiana/**`.
- `estimates.tsv` rows (stic, holt, guest, holt `+img`), from omnios58's
  figures until measured.
- `t2lib/plan.py`: OI guests map to the illumos binaries. Today `osn =
  "omnios" if plat == "illumos"` (plan.py:1667, :1692, :1794); that works
  as long as OI's manifests say `"os": "omnios"`. Decision 4 keeps it so;
  per-OI binaries would need an `openindiana` row there and in
  `build-round.sh`.
- `tests/fixtures/p2/world.py` and the re-blessed goldens.
- **Golden plans:** `holt/tests/corpus/oi2610/` and `oi2610-bios/`, made
  from each guest's dump, with `index.tsv` rows.

Done when two corpus builds have the same sha256, the leak scans are 0, no
other plan changes, and `t2 self-test` and Test2's tests pass.

**BackShot1:** none. The Thinkpad isn't proposed for illumos: its Wi-Fi
and GPU drivers are doubtful there, and the rescue images are wired-only
anyway ([[rescue-wired-only]]).

## What O1 found (2026-11-14)

The media:

- `isos/hipster/20261030/` has the text, GUI and minimal ISOs and USB
  images. `isos/hipster/20261107/` has the supplemental cloud image and a
  Vagrant box, each with a `.sha256sum` and no signature.
- Text ISO: `OI-hipster-text-20261030.iso`, 622,678,016 bytes, SHA-256
  `c5cd9572...`, kernel `illumos-c893fd03ad`.
- Cloud image: `OI-hipster-cloudimage-20261107.img.zst`, 926,163,319
  bytes, SHA-256 `cd56ca63...`, kernel `illumos-f0c8390bf3`. Its
  `build-info.json` names the osnet and userland incorporations it pins.
- Both are in `/bkoscratch/rebuild-inputs/oi2610{,-bios}/` on Chopin and
  Copland, with the SHA-256s pinned in `make-openindiana.sh`.

The system:

- **No os-release at all.** There is neither `/etc/os-release` nor
  `/usr/lib/os-release`. `/etc/release`'s first line is `OpenIndiana
  Hipster 2026.10 (powered by illumos)`. bko-holt's `osrel::read` now
  falls back to it (O2): ID `openindiana`, VERSION_ID `2026.10`.
- **The loader reads only the firmware keyboard**, never the serial port,
  and puts the kernel's console on the screen. The ISO's serial port runs
  at 9600 baud and the installed system's at 115200.
  - `install.py` types into the loader through QEMU's monitor (`sendkey`):
    `3` for the loader prompt, then `set console=ttya`, `set
    os_console=ttya` and `boot`.
  - After that, the serial console works both ways.
  - The first boot writes `/boot/conf.d/serial`, so later boots stay on
    ttya.
- **The text installer over serial:**
  - It asks for the keyboard (47, US-English) and the language (1), then
    shows a menu: 4 sets the terminal type (`xterm`), 1 installs, 3 opens
    a shell.
  - The curses screens are Welcome, Disks, a GPT warning (Tab moves to
    Continue), "Use the whole disk (EFI)", Network (the host name field,
    then "Automatically"), Time Zone (UTC first), Date and Time, Users
    (root's password and a user), Summary, and Installation Complete.
  - Keys: F2 is `ESC O Q` and the arrows are `ESC O A/B` (the installer
    switches the terminal to application-cursor mode).
  - The installer's "Esc-2" form **crashes it** (a TypeError in
    `curses.ascii.isdigit`) when an edit field has the focus. So does
    `ESC [ B`, so only real function keys are used.
  - nwamd's console messages scroll the screen, so the driver matches
    screen text, not the key legend. The first key a screen gets is
    sometimes lost, so `press()` sends it again while the screen is
    unchanged.
  - A whole unattended build takes ~15 min, on Copland.
- **The installer's layout:**
  - GPT from `zpool create -B`: s0 is the 256 MiB ESP (tag 12, unmounted),
    s1 is rpool, s8 is reserved.
  - Datasets: `rpool/ROOT/openindiana` (/, canmount=noauto, bootfs) with
    **`rpool/ROOT/openindiana/var`** (/var, noauto, mounted),
    **`rpool/dump`** (1.5 GiB, the dedicated dump device),
    `rpool/swap` (1.6 GiB) and `rpool/export/home/tester`.
- **No NVRAM entry.** OVMF boots `\EFI\BOOT\BOOTX64.EFI` (illumos' EFI
  loader) from the ESP, including with blank variables (checked with
  `vm-run --uefi`).
- **root is a role** and can't log in on the console ("Roles can only be
  assumed by authorized users"). The first boot logs in as `tester`, runs
  su, then `rolemod -K type=normal root` and puts in the key.
- The network is NWAM (`network/physical:nwam`, `vioif0/_b` by DHCP).
  `dns/client` is disabled, and that shows in `svcs -x`.
- **The cloud image** (`oi2610-bios`) has these differences:
  - **No dump device** ("dumps disabled").
  - The BE is `rpool/ROOT/b5afac2a`, with mountpoint=legacy as OmniOS's is.
  - It has a 3 GiB swap zvol and an `ipadm` net0/dhcp4 address.
  - The console is on ttya from `/boot/conf.d/console`.
  - Its agent is `svc:/system/illumos/metadata`, plus `useragent` and
    `userscript`, which depend on it. All three are disabled after the
    first boot.
  - `pkg publisher` is `openindiana.org`
    (https://pkg.openindiana.org/hipster/), the same as on the installed
    system.
- `prtconf -v` shows `efi-systab` on the UEFI guest, so dump's firmware
  test works.

## What O4 and O5 found (2026-11-14)

- **SticTest1:** `testvm/compare/rt-common.sh`'s `rt_dot` called
  `/usr/gnu/bin/stat`, which OpenIndiana doesn't have. Its gnu-coreutils
  puts GNU stat at `/usr/bin/stat`, since illumos has none. With that
  fallback, `oi2610` gives omnios58's results.
- **HoltTest1's backup:** ht1-bsd.sh's mount script mounted the bootfs and
  then ran `zfs mount -a`, which skips `canmount=noauto`. So the BE's own
  `/var` wasn't backed up, the restored `/var` was empty, and the restored
  system stopped at `filesystem/minimal` ("failed to mount /var/run"). Now
  it mounts the bootfs's children that were mounted at dump time, as load
  does.
- **OmniOS's `bootadm` deletes OpenIndiana's `/etc/versions/build`.**
  - OmniOS's `update_archive()` calls `assemble_files(root, "etc/versions",
    "etc/versions/build", "build.")`. That concatenates `build.*`
    fragments over `build`, and **unlinks** `build` when `etc/versions`
    holds none. Upstream illumos-gate has no such step.
  - It also puts the old mtime back only when its nanoseconds aren't zero,
    and OpenIndiana's packaged file has zero nanoseconds.
  - Load's boot stage (`other/load.rs` `illumos_boot`) now does this:
    1. it copies `build` to a fragment `build.bko-holt` (`cp -p`);
    2. if build's mtime has zero nanoseconds, it moves the mtime up 1 ns,
       which keeps the second;
    3. it runs `bootadm`;
    4. it puts the exact mtime back with `touch -r`;
    5. it removes the fragment and restores `etc/versions`' times.
  - bootadm's filestat records whole seconds only (`cmpstat`, `st_mtime`),
    so the boot archive stays in step. The image has GNU stat and touch:
    kayak's miniroot has `file/gnu-coreutils`, and `keep.backotter` keeps
    `/usr/gnu/bin/*`.
- **UEFI on illumos works as the spec said:**
  - the restored ESP comes back byte for byte with the table;
  - the firmware boots `\EFI\BOOT\BOOTX64.EFI` with blank variables;
  - `bootadm update-archive` is the only boot command.
- **vm-update:** a first update of either guest is ~1 h of downloads from
  pkg.openindiana.org, and leaves a 5-10 GiB overlay, most of it pkg's
  download cache. `in-guest.sh` now sets `flush-content-cache-on-success`
  and keeps two BEs.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| O1 | Fetch the 2026.10 text ISO and its sums into rebuild inputs on Chopin (the user's OK first), and pin the SHA-256. Read the live system offline: os-release, the installer and whether it has an answers mode, the loader's serial setting, the default host name. Then `make-openindiana.sh oi2610` (UEFI) by Decision 2's path, `rebuild-one.sh`, a copy to Cinqcent (the same SHA-256), and `vm-update --try` with BE pruning. Settle every *check* that O1 can, and write "What O1 found" here. Done when it boots alone on both hosts, SSH works and `--try` is ok. **Done 2026-11-14:** both media pinned; oi2610 by the text installer over the console (~15 min, Copland), qcow2 c0a6aec5...; `vm-update --try` ok (255 packages) | 1.25 d (+0.5 d on path 2) |
| O2 | bko-holt (a High-effort subagent): the `SUPPORTED` row and its tests; `illumos.rs`'s comment; a `GENERIC_HOSTS` word if needed; whatever O1's dump shows (UEFI firmware detection, the dump zvol, the BE's mountpoint); an OI fake tree in `holt/src/scenarios.rs` with an ESP, two BEs and a dump zvol. Done when `cargo test` passes and the scenario's plan makes the dump volume, mounts only the bootfs BE, runs `bootadm update-archive` and runs no `installboot` on UEFI. **Done 2026-11-14** (06ce8fa): /etc/release fallback, the SUPPORTED row, the inactive-BE note, scenarios; and from O5, etc/versions/build kept through OmniOS's bootadm | 0.5 d |
| O3 | `oi2610-bios` from the cloud image: `make-openindiana.sh --cloud`, using OI's own agent's seed (version 1 network-config) the way `make-omnios.sh` uses cloud-init. Then its rebuild lines, and both guests' golden plans and `index.tsv` rows. **Done 2026-11-14:** oi2610-bios qcow2 b3441809...; `vm-update --try` ok (225 packages); golden plans oi2610 and oi2610-bios (run `o1`) | 0.4 d |
| O4 | SticTest1 `oi2610`, both archivers, both hosts, 0 FAIL; `ldd` of rustic-bko settles Decision 4. **Done 2026-11-14:** 23 PASS, 2 LIMIT (omnios58's), 0 FAIL on cinqcent and chopin (`oi4-*`), after rt-common.sh's GNU stat fallback; the omnios58-built binaries run as they are | 0.35 d |
| O5 | HoltTest1I with both guests on both hosts: the `oi*)` case in `ht1-bsd.sh`, judge rules for what O5 finds, the alone boot under blank OVMF vars, `ram-sizes.tsv` rows, and one `+img` run with the illumos image. **Done 2026-11-14:** both PASS on chopin (`o5d`) after two fixes (ht1-bsd's backup mounts the BE's /var; load keeps etc/versions/build and its mtime); and on cinqcent: oi2610-bios `o5e`, oi2610 `o5f` (its `o5e` failed only boot state: the source's `network/ipfilter` not up yet at the check, not the restore's) | 0.5 d |
| O6 | The Test2 registrations, in the batched pass | 0.25 d |
| O7 | Docs: holt/README.md's supported list; `stick/illumos/README.md` and `zfs-images.md`'s line about the image restoring OI; the holttest1d README's 1I row and HoltTest1All; SticTest1's README; `layload.md`'s illumos boot stage (UEFI, the dump zvol); memory (holttest1d's 1I, the stale 1BSD wording, an openindiana-support note) | 0.15 d |
| | *≈ 3.4 d* | |

O2 can start beside O1 from the facts above, but its golden plans (O3)
wait for O1's dump. Each piece is committed on main when its tests pass and
the user says so, and nothing is pushed until the user says so.

**Costs** (est.):

- O1 and O3: a 20-40 min install each, in /mnt/bko-ram under
  `cmp-run.sh --reserve 6 3`, then moved to disk (Chopin's RAM disk is
  contended).
- qcow2 sizes: ~3-5 GiB a guest a host, plus the ISO at ~1 GB a host.
- O4 and O5: about 1 h of queue a host.

## Risks

- **Driving curses over serial.** The text installer may redraw in ways an
  expect driver misreads, or the ISO may not offer a serial console at all
  (*check*). The fallback, path 2, costs +0.5 d and drifts from the real
  installer.
- **OmniOS restoring a newer OI.** The boot archive is made by the rescue
  system's tools from the target's lists, and `gptzfsboot` is written by
  an older `installboot`. If either fails to boot, the remedy is to run the
  target's own binaries in a chroot (`chroot /target bootadm
  update-archive`, `/target/usr/sbin/installboot`). That is a small load
  change, but it departs from the OmniOS path.
- **A future ZFS feature.** OI follows gate master. When gate adds a
  feature that r151058 lacks and OI pools enable it, the plan's
  `zfs_problems` check refuses the restore. The remedy then is a newer
  OmniOS base for the image, not an OI image.
- **Binary compatibility.** If rustic-bko built on r151058 links a library
  OI doesn't have at the same version (OpenSSL, say), OI needs its own
  build (Decision 4's fallback, +0.4 d).
- **Rolling churn.** Hipster updates can break; vm-update stops and names
  it. ISOs are kept two years on the mirrors, and our rebuild inputs keep
  ours.
- **BE growth.** Without pruning, guests and dumps grow with each update.

## Out of scope

- The GUI live ISO and its desktop, `gui-install`, the minimal ISO.
- Secure Boot (the illumos loader isn't signed).
- An OI-based rescue image ([[one-kernel-rescue]]).
- Restoring inactive BEs as clones (Decision 3), and their contents unless
  Decision 3 says otherwise.
- Zones, LX branded zones, and non-root pools beyond what generic ZFS
  covers.
- BackShot1 and BackShot2 on OI.
- SPARC, and the old OpenIndiana releases (151a and others).

## Decisions for the user

1. **The ISO:** the **2026.10 text installer** with its SHA-256 pinned
   (recommended: it is for servers, small, and has a console installer);
   or the GUI live ISO (large, graphics-first); or the minimal ISO (it may
   lack sshd: *check*).
2. **How the guests are built:** **the text installer driven over the
   serial console** (recommended: the real layout, BE name, swap and dump
   zvols); or by hand from the ISO's shell (+0.5 d, drifts); or one GUI
   install kept as a pinned image (not rebuildable).
3. **Inactive boot environments:** **keep OmniOS's behaviour**, where an
   inactive BE comes back as a plain, empty dataset and only the booted BE
   is restored, and say so in a plan note (recommended: it is the
   precedent the judge already rules on, and an older BE is a rollback
   point, not data); or back up and restore every BE's files as a plain
   dataset (more data, and each BE bootable again but no longer a clone);
   or leave inactive BEs out of the restore entirely, so that `beadm list`
   doesn't show empty ones.
4. **Binaries:** **reuse the illumos binaries built on omnios58**, with
   OI manifests saying `"os": "omnios"` (recommended: illumos is
   compatible from older to newer, and there is one illumos round); or
   build restic-bko and rustic-bko on an OI guest as their own platform
   (+0.4 d, and a new row in every build round).
5. **The supported version:** `("openindiana", "*", "OpenIndiana")`
   (recommended: Hipster is rolling, and a snapshot is only an ISO date);
   or the snapshot's year (`2026`), re-checked each year.
6. **Guests:** **`oi2610` (UEFI) and `oi2610-bios`**, with SticTest1 on
   `oi2610` only (recommended: UEFI is new for illumos, and BIOS tests OI's
   own boot blocks under OmniOS's installboot); or only `oi2610` (UEFI),
   with BIOS left to omnios58; or a mirror guest too (the same code as
   `omnios58-mirror`: not worth it).
7. **HoltTest1's group:** **join 1I** (recommended: it is "illumos", and
   both distributions share the rescuer and the code); or a new **1OI**;
   or 1BSD (no: illumos left 1BSD on 2026-11-05).
8. **Names:** **`oi2610`** and `oi2610-bios` (recommended: short, like
   `omnios58`; the number is the snapshot); or `openindiana2610`.
