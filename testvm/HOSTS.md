# The KVM test hosts

The test scripts run guests on four amd64 KVM hosts. Two are big, Chopin and
Cinqcent, and two are small, Copland and Cage (the M900s). A fifth small host,
Czerny, is due at the end of November 2026 and has a reserved row already.
Bigfoot, the user's ARM64 machine, is the one arm64 host (since 2026-11-14,
docs/design/arm64.md): it runs only arm64 guests, in a systemd slice.
Every script takes the hosts from one table, `testvm/hosts.json`. This page
describes that table, what each class of host runs, and how a new host joins.

| host | class | slot range | drive.py ports | roles | default |
|---|---|---|---|---|---|
| chopin | big | 2700-2799 | 2780-2799 | build, fleet, store-mirror, bigfoot-link | yes |
| cinqcent | big | 2900-2999 | 2980-2999 | build, cache | yes |
| copland | small | 2800-2899 | 2880-2899 | cache | no |
| cage | small | 3000-3099 | 3080-3099 | cache | no |
| czerny | small | 3100-3199 | 3180-3199 | cache | no (row reserved: `enabled: false`) |
| bigfoot | small, arm64 | 3200-3299 | 3280-3299 | none | no |

## The table: testvm/hosts.json

There is one row per host. The row order matters: lists keep it, and the first
host with the fleet role (Chopin) holds vm-update's fleet lock.

| field | meaning |
|---|---|
| `name` | the short host name. It must be exactly what `hostname -s` prints, because guest-slot.sh and run-all.sh look the host up by it (`H` or `H.DOMAIN`, never by prefix). |
| `ssh` | `USER@HOST`, the ssh destination used by sync-hosts.sh, build-round.sh, promote-bins.sh, vm-update, bs2 and drive.py. t2 itself always uses `m@NAME.local`. |
| `ip`, `host_key_alias` | an address to use when mDNS fails, and the name known_hosts pins that address under (`host_key_alias` defaults to the host part of `ssh`, e.g. `chopin.local`). `ip` is null in every row today. Only drive.py uses them: when ssh to the name fails because the host can't be resolved or reached, drive.py tries `-o HostKeyAlias=ALIAS USER@IP` (hosts.py `ssh_fallback`) and, if that answers, reaches the host that way for the rest of the run. t2, the shell scripts, vm-update and bs2 don't use the fallback yet. |
| `slot_range` | `LO-HI`, the host's guest-slot range (`slot_range_here` in guest-slot.sh). run-all.sh's `PORT_BASE` is LO and its `GUEST_PORTS` is the whole range. Ranges must not overlap. |
| `stic_ports` | `[LO, HI]`, drive.py's ports for the checks. They must lie inside `slot_range`. |
| `class` | `big` or `small` (see below). The gate's `host_reserve` in `gate/layers.json` and run-all.sh's profile are chosen by class. |
| `roles` | `build`: builds round binaries (stick/build-round.sh) and bko-holt. `fleet`: holds every guest in a full `vms/`, kept alike by vm-update. `store-mirror`: vm-store's mirror, the only publisher. `bigfoot-link`: the one host that talks to Bigfoot. `cache`: has a vm-store cache. |
| `default` | a host of every run without `--hosts` (t2, drive.py). A default host must be enabled. |
| `enabled` | `false` marks a reserved row: the name and ranges are taken, but no script treats it as a host. |
| `arch` | optional: `amd64` or `arm64`, the architecture of the host's guests and binaries. A row without it is `amd64`, as every host was before 2026-11-14. A guest has one too, by its name: `arm64` for a guest named `*-arm64`, else `amd64` (hosts.py `guest_arch`). A guest runs only on a host of its own architecture (see below). |
| `job_slice` | optional: a systemd slice (`bko.slice`) that every heavy job on the host runs in, or null for none. hosts.py `job_prefix` builds the words that start one: `sudo -n systemd-run --scope --quiet --slice=SLICE -- setpriv --reuid=USER --regid=USER --init-groups --reset-env -- CMD`. vm-run starts itself again that way when it isn't in the slice, with its own environment and working directory. cmp-run.sh puts each job's scope in the slice (`systemd-run --slice`). |

Readers:

- Python: `testvm/test2/t2lib/hosts.py`. It is stdlib only and imports nothing
  from t2lib. drive.py, vm-update and bs2 load it by its path, and t2lib
  imports it. Its command line is `hosts.py
  names|get|limit|phrase|arch|guest-arch|job-prefix|check`, and `names`
  takes `--arch A`. `get` prints `amd64` for a row without `arch`.
- Shell: `testvm/hosts.sh`, sourced by bash (3.2 is enough). It provides
  `hosts_names`, `hosts_get`, `hosts_phrase`, `hosts_limit`, `hosts_arch`,
  `hosts_guest_arch` and `hosts_job_prefix`, which call hosts.py with
  `python3 -I -B`.

Both readers find the table relative to themselves: the checkout's `testvm/`
here, and `~/backotter-work/testvm` on the hosts. `BKO_HOSTS_JSON` names a
different table (for tests). The table is checked strictly: unique names, no
unknown fields, ranges that don't overlap, ports inside the range, known
classes and roles, and at least one default host. A table that fails these
checks stops every script that reads it. Nothing falls back to built-in host
names. Check it by hand with `python3 testvm/test2/t2lib/hosts.py check`.
Run the command from the repo root.

These scripts take their hosts from the table. Where a list would mix
architectures, each takes only the hosts it can use:

- t2's `KNOWN_HOSTS` (the enabled hosts, bigfoot too) and `DEFAULT_HOSTS`.
  Placement keeps each cell to hosts of its guests' architecture (below).
- the gate's per-host loop cells. The gate's layers run amd64 binaries, so a
  gate cell on bigfoot is not runnable until its own layers exist (A6).
- drive.py's `HOSTS` and the default for `--hosts`. On each host it leaves out
  the images of another architecture's guests, and it refuses a guest whose
  architecture no host of `--hosts` has.
- the ranges in guest-slot.sh and run-all.sh. run-all.sh refuses an arm64
  host, and its `SPLIT=all` means every enabled amd64 host.
- the default host list in sync-hosts.sh: every enabled host, bigfoot
  included, since it copies code only. A bare table name (`sync-hosts.sh
  bigfoot`) means that row's ssh.
- build-round.sh's ssh targets and its build-role check. `--dist-hosts all`
  means every amd64 host, and naming an arm64 host is refused: a round's
  binaries are amd64. promote-bins.sh is the same.
- vm-update's fleet (`ALL_HOSTS`, the hosts with the fleet role). A guest's
  copies are merged only on hosts of its architecture.
- bs2's ssh targets: the amd64 hosts. `--hosts bigfoot` is refused, because
  BackShot2's sources are amd64.
- t2 holt's bko-holt shipping with `holt_ship known`: the amd64 hosts.
- ht1-queue.sh: on each host it skips the lines of another architecture's
  guests (`uname -m`).

vm-store doesn't read the table. Bigfoot is vm-store's master, which only
Chopin talks to (the `bigfoot-link` role), by its own configuration
(`VM_STORE_BIGFOOT_HOST`, default `m@bigfoot.local`). Bigfoot's row has no
store role, and `/bkoscratch` there holds no `store.json`, so no cache or
mirror logic treats it as one.

Two hosts.json changes re-key things:

- **Editing hosts.json** (to enable or default a host, or change a range)
  re-keys the gate's `loop:linux` and `loop:sparse` cells. The file is in
  their harness.
- **Editing any of the three table files** (hosts.json, hosts.sh,
  t2lib/hosts.py) changes the stic plumbing digest. The three files are stic
  plumbing in `stic/manifests.json`, so the stic canaries become due.

## Big and small hosts

**Big hosts** (Chopin, Cinqcent): 62 GiB of memory and a 30G RAM disk. Their
cmp-run.sh ceiling is about 29.0 23. They have host OpenZFS, and both have the
build role. Only Chopin has the fleet role (since 2026-11-13). Cinqcent is a
cache host with every guest: most of its `vms/` entries are links into its
vm-store cache (cap 150G, floor 50G), and the rest are full folders: the
guests whose disks back kept qcow2 overlays outside `vms/`, the 21 versions
published 2026-11-13 (relinked once Bigfoot has them), and
freebsd15-builder (a link to imgbuild). A publish reaches its links by
`vm-store link --all`, not by vm-update, so its full folders lag Chopin's
after an update until they are relinked.

vm-update updates every guest it can reach over SSH. Sculpt OS
(`sculpt2610`) has no sshd: its `no-ssh` makes vm-update refuse it, and
`testvm/sculpt-update` updates it on Chopin instead, offline, writing what
Sculpt's own System Update writes (the newest point build of its release
from Genode Labs' signed image index). It keeps `image-state` in
vm-update's format, so the new generation reaches the cache hosts the same
way: `vm-store publish sculpt2610` on Chopin (`sculpt-update --publish`
does it), then `vm-store link sculpt2610` on each cache host whose
`vms/sculpt2610` is a link. `sculpt-update --check vms/sculpt2610` says,
on any host, whether a newer image or release is out.

**Small hosts** (Copland, Cage, and Czerny later): 27 GiB of memory and a 10G
RAM disk. Their ceiling is 9.0 9.2. They have no host ZFS. Their `vms/` is a set
of links into the vm-store cache, which holds only the published guests (21
guests are not published yet).

The ceiling is the most that one `cmp-run.sh --reserve` can ever get on a host.
`cmp-run.sh --ceiling` prints it as `DISK MEM` and writes nothing.

Placement works from capabilities, not host names. t2's facts probe
(`t2lib/facts_host.py`, sent over ssh from the Mac's checkout, read only)
records each host's guests, ceiling and `caps`:

- `zfs`: a zpool binary is installed. `zpool` gives its path, and `zfs_module`
  says whether the module is loaded.
- `holt_rescuer`: `/bkoscratch/holttest1d/rescuer/rescuer.qcow2` exists.
- `holt_debs`: `/bkoscratch/holttest1d/bin` has the debs.

`plan.py` keeps a stic or holt cell off a host that cannot run it, and says
why. The plan's reasons then include, for example, "kept off copland: no host
OpenZFS". A cell that no host of the run can take is not runnable, and the
plan says so. A cell is kept off a host for any of these reasons:

- **Another architecture.** The host's `arch` is not the architecture of
  the cell's guests: an `-arm64` guest's cells never go to an amd64 host,
  and no other guest's cells go to bigfoot. Facts whose `machine` disagrees
  with the table keep the host off too. This needs no facts.
- **The guest is missing.** A host without the fleet role lacks a guest the
  cell boots. A fleet host is assumed to hold every guest.
- **No host ZFS.** The cell's source is in `classes.json` `holt.needs_host_zfs`:
  cachyos-zfs, chimera-zfs, debian13-zfs, ubuntu24, ubuntu26-zfs,
  ubuntu26-zfsmirror. This
  applies to every variant, `+img` included.
- **No rescuer or debs.** A holt cell needs HoltTest's rescuer, its debs, or
  both, and the host lacks one it needs. A Linux source needs both, unless it
  is UEFI and the host has rescuer-uefi.qcow2, when it needs only the debs.
  redox and sculpt2610 need only the rescuer. `+img` and the `bsd` kind
  (the BSDs, OmniOS, Haiku) need neither.
- **Over the ceiling.** The cell's reservation is over the host's ceiling.

Old facts without `caps` skip the caps checks. The facts also record
`machine`, from `uname -m`. The identity check asks each
host only about the guests it should hold, so a cache host that lacks a guest
doesn't fail that guest everywhere.

The runners check the same things on the host before starting a job:

- **drive.py** never starts a guest on a host without its image. A guest that
  no suitable host has is named NOVM.
- **ht1-queue.sh** skips every todo line this host can never run: over the
  ceiling, no image, a ZFS guest without zpool, or no rescuer or debs. It puts
  a line back on the todo (`--putback`) when cmp-run.sh refuses its reserve as
  one that can never fit.
- **ht1.sh** refuses to start (exit 2) without the rescuer or the debs. It no
  longer falls back to `vms/debian13` without saying so.

**What stays on the big hosts**, also after the small hosts become default
hosts:

- guests that are not published to the store;
- the host-ZFS holt sources;
- round, bko-holt and image builds (the build role);
- cells over 9.0 9.2: for example stic debian13-zfs (`--reserve 13 5`) and
  ubuntu26-zfsmirror (23 GiB). The gate's per-host loop cells are the
  exception: they run on a small host too, with that class's smaller
  `host_reserve` (`loop:linux` 8.5 4, `loop:sparse` 6 4);
- the holt pins in `classes.json`: openbsd8 on Chopin, and dragonfly6 and redox
  on Cinqcent. The redox tools are also on Cage and Copland, but not on Chopin.
- UEFI holt sources, which run only where `rescuer-uefi.qcow2` exists
  (Cinqcent);
- the fork gates (`fork:*` pinned in `gate/layers.json`);
- run-all.sh's guest platforms. On a small host run-all.sh runs only linux and
  sparse.
- vm-update's fleet, the store mirror and the Bigfoot link (all Chopin).

## Bigfoot, the arm64 host

Bigfoot (`m@bigfoot.local`, 192.168.68.197 as its `ip`) is the user's daily
machine: a desktop session, Plex and deluge run there. Its row is small class,
`arch: arm64`, `job_slice: bko.slice`, with no roles and `default: false`. It
stays out of default runs until HostTest2 passes on it
(docs/design/arm64.md, A6).

- **Staging.** Everything bko keeps there is under `/home/m/bko-arm64`.
  `~/backotter-work` and `/bkoscratch` are symlinks into that folder.
  DantePool, `/bt` and the vm-store master are never touched.
- **The slice.** Every guest and cmp-run.sh job runs in `bko.slice`, a
  system slice limited to the Cortex-A720 cores (CPUs 0-1 and 6-11),
  CPUQuota 600%, MemoryMax 24G and IOWeight 20. vm-run and cmp-run.sh put
  their jobs in the slice themselves. Run as the user, vm-run starts itself
  again under `sudo -n systemd-run --scope --slice=bko.slice -- setpriv ...`.
- **vm-run** on an aarch64 host boots QEMU's `virt` machine with AAVMF, and
  UEFI is always on. Without `--uefi`, Secure Boot is off; `--uefi` turns it
  on with Microsoft's keys. `--bios` and `--q35` are refused. Outside the
  slice, on cores of more than one kind, vm-run pins QEMU to the most
  numerous kind (see the vm-run header).
- **Sync.** `testvm/sync-hosts.sh bigfoot` copies testvm/ and stick/ there,
  and the default run includes it.

## Adding a host

Czerny's row is already in the table (3100-3199, ports 3180-3199, small,
cache, `default: false`, `enabled: false`). Adding it means one edit,
`"enabled": true`, followed by the provisioning steps below. For any other new
host, add a row first:

1. Choose an unused 100-port range.
2. Set `stic_ports` to the last 20 ports of that range.
3. Set `name` to the host's `hostname -s`.
4. Set `class` and `roles`, with `default: false`.
5. Run `python3 testvm/test2/t2lib/hosts.py check`.

The table is read on the Mac and on the hosts, so commit the row on main, and
sync it, before anything runs there.

Provisioning is done by the user, or by a session the user allows to write to
the hosts. Run it from main only (never from a worktree), without disturbing
other sessions' runs.

1. **Base.**
   - User `m` in `kvm`, with the same passwordless sudo as on Cage and Copland
     (run-all.sh and ht1-queue.sh call `sudo -n`).
   - `/mnt/bko-ram`, a 10G RAM disk on a small host.
   - `/bkoscratch` owned `m:m`, mode 755, with at least 80 GB free.
   - python3 3.9 or newer, rsync, flock, sha256sum, QEMU and OVMF.
   - jq isn't needed.
   - `hostname -s` must print the row's name.
2. **SSH.**
   - The Mac to `m@NAME.local`, with BatchMode.
   - The new host to Chopin, for vm-store fetches, the holt queue host and
     vm-update's fleet lock.
   - Chopin and Cinqcent to the new host, for build-round.sh `--dist-hosts` and
     promote-bins.sh. Both copy host to host, and go through the Mac when they
     can't.
   - Between the small hosts too, if they are to reach each other. Cage and
     Copland can't today: cage's key isn't in copland's authorized_keys, and
     copland fails cage's host key.
   - Every host's known_hosts must have the new host's current key and address.
     Three hosts still pin `copland.local` to an old IP, 192.168.68.52.
   - Fill in `ip` and `host_key_alias` if mDNS is unreliable there. Only
     drive.py falls back to them so far.
3. **Sync.** Run `testvm/sync-hosts.sh` from main. With no arguments it covers
   every enabled host, so the new one is included once it is enabled. You can
   also name it: `testvm/sync-hosts.sh m@czerny.local`.
4. **Store cache.**
   - Create the cache: `vm-store init /bkoscratch/bko-cache --role cache --cap
     SIZE --floor SIZE`. Size the cap for every linked guest plus one new
     version. For comparison, Cage's and Copland's 160G caps are 143G full.
   - Link each guest the host should have: `vm-store link GUEST...`. This
     fetches from Chopin and creates `vms/GUEST`.
   - After every publish, run `vm-store link --all` on each cache host. Nothing
     runs it automatically yet. See `testvm/store/README.md`, "Cache hosts'
     vms/: link".
5. **HoltTest inputs.** Copy `/bkoscratch/holttest1d/bin/` from Chopin:
   bko-holt, the f2fs-tools and jfsutils debs, and bcachefs. Copy
   `rescuer/rescuer.qcow2` and its `.sha256` from Chopin or Cinqcent. Copy
   `rescuer-uefi.qcow2` from Cinqcent if UEFI holt cells should run there.
   Check the sums with `sha256sum -c`. No script does this step yet. Without
   these files the host is still used for BSD holt lines and `+img`, and its
   runner skips Linux lines (see `testvm/holttest1d/README.md`, "Queues").
6. **Compare disks.** Run `mkdir -p /bkoscratch/compare-disks`. cmp-run.sh's `!`
   lines put restored disks there, and no script creates it.
7. **Binaries.** First run `testvm/promote-bins.sh --dry-run NAME`, then
   `testvm/promote-bins.sh NAME`. This copies bko-bin, cmp-bin and holt-bin
   from Chopin, each file checked by sha256. Then, for each round still in use,
   run `stick/build-round.sh --dist-only --dist-hosts NAME ROUND`. A
   SticTest2 run refuses a ROUND that a host in use lacks.
8. **Facts.** Run `testvm/test2/t2 SticTest2All plan --hosts
   chopin,cinqcent,NAME`, and the same for `HoltTest2All` and `GateTest2`.
   Each plan probes the new host's facts (guests, ceiling, caps) and writes
   nothing. Its reasons show which cells it keeps off the new host and why
   (`kept off NAME: ...`). On the host, check that
   `testvm/compare/cmp-run.sh --ceiling` prints the expected `DISK MEM`.
9. **Trial runs.** Run GateTest2 (the HostTest2 layers) and one representative
   per family with `--hosts` naming the new host. For a small host, these are
   the cells that fit it.
10. **Join the defaults.** Set `"default": true` in hosts.json, then update the
    tests that pin today's two default hosts: `tests/test_hosts.py`,
    `tests/test_gate_hosts.py` and drive.py's self-test. This is a code change.
    Only then do t2, drive.py and SweepTest2's completeness check include
    the host without `--hosts`. vm-update's fleet stays the
    hosts with the fleet role.

The join rule is in `docs/design/test2/design-classes.md` §2.6.
