# The KVM test hosts

The test scripts run guests on six amd64 KVM hosts. Two are big, Chopin and
Cinqcent, three are small, Copland, Cage and Czerny (the M900s), and the sixth
is Choubox, smaller still. Czerny (a MoreFine M900 like Cage: Ryzen 7 H 255,
16 threads, 27 GiB, a 10G RAM disk, XFS root, Debian 13 on trixie-backports
with ZFS and bcachefs) was enabled on 2026-11-19 with Cage's roles and its own
range 3100-3199, and joined the defaults the same day by the user's choice
(step 10 below). It has the small class's limits, as Cage and Copland do.
Choubox (the user's GMKtec NucBox G3 Pro: i3-10110U, 4 threads, 15.5 GiB, one
512 GB SATA SSD) joined the table on 2026-11-17 and the defaults the same day,
by the user's choice, before its trial runs were done. They stood so: the
gate's bin cells (abi, static, version) PASS; the holt alpine324, artix-s6,
debian13 and netbsd11 lines PASS, freebsd15 FLAKY-FAIL (an ssh failure in
HoltTest's edge checks, fixed the same day) and haiku6 HARNESS (over its
RAM-disk estimate; its row in ram-sizes.tsv is raised); the stic mint22 and
aerynos checks PASS, and its other 12 stic checks never started (the rounding
stall fixed in 3b00344). Its two HostTest2 loop cells can never fit its
3.0 GiB disk ceiling. The user waived the rest. It is small
class with limits of its own in its row: 1 SticTest slot, 1 HoltTest job,
1 BackShot2 slot and `base_gib` 3, with a 4G RAM disk and the ZFS ARC capped at
512 MiB (`/etc/modprobe.d/zfs-bko.conf`), so its `cmp-run.sh --ceiling` is
3.0 6.0. That fits the `--reserve 3 6` SticTest guests and the HoltTest sources
of D 3 or less; nothing else is placed there. Since 2026-11-18 its row also sets
`max_wall_x` 2: t2 leaves to the other hosts the HoltTest2 and GuestTest2 cells longer than
twice their family's median cell elsewhere, and those it ran over twice as long as elsewhere
lately (testvm/test2/README.md, How a plan decides). Its role is `cache` only (cap
300G since 2026-11-19, was 260G; floor 60G on the /home partition, `/bkoscratch` a bind of
`/home/bkoscratch`; keep the floor: the user's projects share that disk). Since
2026-11-25 (the user's choice) it doesn't link the eight large desktops it could
only run on its SATA SSD by HoltTest's overflow: mint22, oi2610,
elementary8-luks, pikaos4, pikaos4-luks, slackware15, slackware15-uefi and
zorin18; t2 places their cells on the other hosts, as for any guest a cache
lacks. They are choubox's `cache_skip` in hosts.json, so neither a publish's
relink nor `vm-store relink-caches` links them there again (reported "skipped");
to bring one back, take it out of `cache_skip`, then `vm-store relink-caches G
--hosts choubox`. Its
Debian also holds the user's own projects, and it is the user's fallback
bare-metal machine for real-world runs when a distro rejects the Thinkpad: such
a run wipes it and takes it out of the farm until it is provisioned again.
Bigfoot, the user's ARM64 machine, is the one arm64 host (since 2026-11-14,
docs/design/arm64.md): it runs only arm64 guests, in a systemd slice.
Every script takes the hosts from one table, `testvm/hosts.json`. This page
describes that table, what each class of host runs, and how a new host joins.
Before starting heavy work by hand, check `testvm/test2/t2 batch status`: a running `t2 batch` holds whole pools on its hosts (testvm/test2/BATCH.md).

| host | class | slot range | drive.py ports | roles | default |
|---|---|---|---|---|---|
| chopin | big | 2700-2799 | 2780-2799 | build, fleet, store-mirror, bigfoot-link | yes |
| cinqcent | big | 2900-2999 | 2980-2999 | build, cache | yes |
| copland | small | 2800-2899 | 2880-2899 | build, cache | yes |
| cage | small | 3000-3099 | 3080-3099 | build, cache | yes |
| czerny | small | 3100-3199 | 3180-3199 | build, cache | yes (since 2026-11-19) |
| choubox | small (own limits) | 3300-3399 | 3380-3399 | cache | yes (since 2026-11-17) |
| bigfoot | small, arm64 | 3200-3299 | 3280-3299 | build-arm64 | no |

## The table: testvm/hosts.json

There is one row per host. The row order matters: lists keep it, and the first
host with the fleet role (Chopin) holds vm-update's fleet lock.

| field | meaning |
|---|---|
| `name` | the short host name. It must be exactly what `hostname -s` prints, because guest-slot.sh and run-all.sh look the host up by it (`H` or `H.DOMAIN`, never by prefix). |
| `ssh` | `USER@HOST`, the ssh destination used by sync-hosts.sh, build-round.sh, promote-bins.sh, vm-update, bs2 and drive.py. t2 itself always uses `m@NAME.local`. |
| `ip`, `host_key_alias` | an address to use when mDNS fails, and the name known_hosts pins that address under (`host_key_alias` defaults to the host part of `ssh`, e.g. `chopin.local`). `ip` is set in Chopin's row (192.168.68.194, since F10) and Bigfoot's. drive.py, pick-host and rustic-exact-round.sh use them; drive.py and pick-host as a fallback: when ssh to the name fails because the host can't be resolved or reached, drive.py tries `-o HostKeyAlias=ALIAS USER@IP` (hosts.py `ssh_fallback`) and, if that answers, reaches the host that way for the rest of the run; rustic-exact-round.sh reaches a host whose row has an `ip` by it always (as it reached Chopin before F10). t2, the other shell scripts, vm-update and bs2 don't use them yet. |
| `slot_range` | `LO-HI`, the host's guest-slot range (`slot_range_here` in guest-slot.sh). run-all.sh's `PORT_BASE` is LO and its `GUEST_PORTS` is the whole range. Ranges must not overlap. |
| `stic_ports` | `[LO, HI]`, drive.py's ports for the checks. They must lie inside `slot_range`. |
| `class` | `big` or `small` (see below). The gate's `host_reserve` in `gate/layers.json` and run-all.sh's profile are chosen by class. |
| `roles` | `build`: builds a round's binaries (stick/build-round.sh deals its build units over every host with the role, all four amd64 hosts since 2026-11-14) and bko-holt (holt.py builds those on the big build hosts only: a small host has no room for one beside the run's cells; promote-bins.sh's source is a big one too). `build-arm64`: builds a round's arm64 rustic-bko natively (`build-round.sh --arch arm64`) and arm64 bko-holt (`build-holt.sh linux-arm64`); only an arm64 host has it, and it is not `build`, so every amd64 build loop leaves the host out. `fleet`: holds every guest in a full `vms/`, kept alike by vm-update. `store-mirror`: vm-store's mirror, the only publisher, and the caches' default fetch source (vm-store mirror_sources, unless VM_STORE_SOURCES is set). `bigfoot-link`: the one host that talks to Bigfoot. `cache`: has a vm-store cache. |
| `default` | a host of every run without `--hosts` (t2, drive.py). A default host must be enabled. |
| `enabled` | `false` marks a reserved row: the name and ranges are taken, but no script treats it as a host. |
| `arch` | optional: `amd64` or `arm64`, the architecture of the host's guests and binaries. A row without it is `amd64`, as every host was before 2026-11-14. A guest has one too, by its name: `arm64` for a guest named `*-arm64`, else `amd64` (hosts.py `guest_arch`). A guest runs only on a host of its own architecture (see below). |
| `rustup_home`, `cargo_home` | optional: absolute paths, or null. The `RUSTUP_HOME` and `CARGO_HOME` of the host's Rust builds, for a host whose pinned toolchains live apart from `~/.rustup` and `~/.cargo` (bigfoot's, in its staging folder). hosts.py `rust_env`, and hosts.sh's `hosts_rust_env [NAME]`, which exports each one that is unset; build-round.sh, build-holt.sh and stick/zig-tools.sh call it. |
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
- build-round.sh's ssh targets and its build-role check (`--hosts all`, the
  default, is every enabled amd64 host with the role, and the units are dealt
  over whatever hosts it names, by class and capability:
  stick/deal-units.py. `--split legacy` is the old fixed chopin/cinqcent
  split, and takes those two alone). `--dist-hosts all`
  means every amd64 host. An arm64 host named in `--dist-hosts` gets only the
  round's arm64 directories (`bin/linux-arm64/`, with its own SHA256SUMS),
  never an amd64 binary. `--arch arm64` adds the first host with the
  `build-arm64` role (bigfoot) as the round's native arm64 rustic-bko
  builder (docs/design/arm64.md, A2). promote-bins.sh is the same: `all`
  means the amd64 hosts, and an arm64 host named gets only each set's
  `*-arm64/` directories.
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
build role; so do the small hosts since 2026-11-14, but only a big one builds
bko-holt or is promote-bins.sh's source. Only Chopin has the fleet role (since 2026-11-13). Cinqcent is a
cache host with every guest: most of its `vms/` entries are links into its
vm-store cache (cap 150G, floor 50G), and the rest are full folders: the
guests whose disks back kept qcow2 overlays outside `vms/`, the 21 versions
published 2026-11-13 (relinked once Bigfoot has them), and
freebsd15-builder (a link to imgbuild). A publish reaches its links itself:
Chopin's `vm-store publish` runs `vm-store link` on each cache host for the
guests it moved, and `vm-store status --farm` on Chopin shows any host left
behind. vm-update doesn't reach them, and link never replaces a full
folder, so those lag Chopin's after an update until they are made links.
Cinqcent's `vms/` and cache share the CN600 (`/bkoscratch/fast`); with it 98%
full on 2026-11-16, four unpublished full folders (truenas25, truenas25-mirror,
solus49, biglinux) moved to `/bkoscratch/vms-sata` on sda2, each `vms/G` a
symlink there, so the paths are unchanged. Since the 2026-11-25 cutover every
`vms/G` there is a link into the cache (cachyos-zfs, slackware15 and
slackware15-uefi still full folders); its cache is cap 420G, floor 30G (all
the CN600 allows; the cap was 400G) until its test disk arrives (2026-12-01).

vm-update updates every guest it can reach over SSH. Sculpt OS
(`sculpt2610`) has no sshd: its `no-ssh` makes vm-update refuse it, and
`testvm/sculpt-update` updates it on Chopin instead, offline, writing what
Sculpt's own System Update writes (the newest point build of its release
from Genode Labs' signed image index). It keeps `image-state` in
vm-update's format, so the new generation reaches the cache hosts the same
way: `vm-store publish sculpt2610` on Chopin (`sculpt-update --publish`
does it), which then runs `vm-store link sculpt2610` on each cache host.
`sculpt-update --check vms/sculpt2610` says, on any host, whether a newer
image or release is out. `t2 GuestTest2 update` updates it too, by
sculpt-update on Chopin as a job of the round (testvm/test2/README.md), but
doesn't publish: `vm-store publish sculpt2610` on Chopin after the round.
Every cache host links it (`vms/sculpt2610` on Cinqcent, Copland and Cage).

**Small hosts** (Copland, Cage and Czerny): 27 GiB of memory and a 10G
RAM disk. Their ceiling is 9.0 10.1, measured on Copland and Cage on
2026-11-17 with the ZFS module loaded, as it is on each (the module alone costs the ARC's GiB of
headroom; since 3b00344 cmp-run.sh floors the figure instead of rounding it).
Ask the host: `cmp-run.sh --ceiling`. Choubox is small class too, with its own
smaller limits and a 3.0 6.0 ceiling (above).
All three have host ZFS (Copland and Cage since 2026-11-14; `zpool`, 2.4.4 from trixie-backports;
BACKPORTS.md), so `needs_host_zfs` no longer keeps cells off them; their
ceiling still does. Their `vms/` is a set
of links into the vm-store cache, which holds only the published guests (21
guests are not published yet). They are default hosts (Copland and Cage since
2026-11-14, Czerny since 2026-11-19): the big
hosts queued for hours while Copland and Cage idled, and what only a big host can run
is kept there by each cell's ceiling and capabilities, never by a host's name.

The ceiling is the most that one `cmp-run.sh --reserve` can ever get on a host.
`cmp-run.sh --ceiling` prints it as `DISK MEM` and writes nothing; each figure
is a limit of its own. `cmp-run.sh --free` prints, as `DISK MEM` and then
`waiting N`, what a new `--reserve` could get there now (the RAM disk: the
smaller of the ceiling's and budget()'s; the memory: budget()'s, within the
ceiling's two figures together, which is just what admission tests a
`--reserve` against, so the ledger's rows and the live memory check count once
each) and how many jobs are in line; it writes nothing either, so anyone may
ask. Since 2026-11-24 the memory of `--free` (and of budget()) is for a job's
D and M together: a `--reserve D M` within the ceiling starts when D fits the
RAM disk and D + M the memory. A job's RAM disk is memory too, so the memory
rules charge running jobs' RAM disk as reserved (or as written, when more),
not the whole tmpfs as if it were full beside every job's M, which had held
the unreserved RAM disk as memory too (testvm/compare/cmp-run.sh's header). It reads the ledger
unlocked, so its figure is advice: only a `--reserve` holds room. `testvm/pick-host` asks every default host at once, over
ssh, and prints the one to use for a job of your own:

    H=$(testvm/pick-host --reserve 8 5 --guest debian13 --needs debs) &&
      ssh "$H" 'cd ~/backotter-work && ...'

It drops the hosts that can never take the job (its `--reserve` over their
ceiling, a guest or a capability missing) and ranks the rest by the room they
have now, then by load; `--json` shows the whole probe and why each host was
dropped. `testvm/holttest1d/ht1-farm.sh RUN GUEST...` starts one HoltTest1
queue over every default host in one command (README "Queues").

**RAM first, the SSD for overflow** (the user's choice, 2026-11-23). Each host's
`scratch_mode` in the table says where a job that can work on either puts its
scratch. Today that is a HoltTest line (`ht1-queue.sh`); SticTest's, GuestTest's
and the gate's scratch is in the RAM disk as before, since their scripts name
`/mnt/bko-ram` themselves.

| `scratch_mode` | A HoltTest line works |
|---|---|
| `ram` | in the RAM disk, waiting for its room there, as before 2026-11-23 |
| `overflow` | by slots, RAM first: in a RAM slot when one is free and its reservation D fits the RAM disk right now, whatever its size; else in an SSD slot (`/bkoscratch`) when one is free and the SSD has room for its D; else not now: it stays in the todo, for the other hosts or for later, and the next line that can start is taken |
| `ssd` | always on `/bkoscratch` |

No size alone sends a line to the SSD. Until 2026-11-23 evening a cutoff did
(`ssd_cutoff_gib`, 12 GiB: a line over it went straight to `/bkoscratch`), and
in the first run under it, t2h-20261123T1713-3cd9, none of the 17 jobs that
started worked in RAM: 9 on the small hosts were over their RAM-disk ceilings
(D 14 to 24 GiB against 9.0, and 3.0 on choubox), vanilla3 (30) was over
chopin's 29.0, and the other 7 on chopin and cinqcent (D 15 to 27) fitted the
29.0 ceiling, cinqcent's RAM disk standing empty, and went to the SSD for the
cutoff alone. The cutoff is gone from `ht1-queue.sh` (`place`), the planner and
the table. What still puts a line on the SSD on a big host is the room in the
RAM disk as it starts (beside truenas25's 27 GiB little else fits), or a host
with no RAM slot free.

**Slots** (the user's choice, 2026-11-23 evening). In mode `overflow` a host has
RAM slots and SSD slots for its HoltTest jobs, `holt_ram_slots` and
`holt_ssd_slots` in the table; `holt_jobs` stays the total:

| host | `holt_ram_slots` | `holt_ssd_slots` | `holt_jobs` |
|---|---|---|---|
| chopin, cinqcent | 2 | 4 | 4 |
| copland, cage, czerny | 1 | 2 | 2 |
| choubox | 1 | 1 | 1: one job, a RAM job or an SSD job |
| bigfoot (mode `ram`) | not counted | none | 1 |

A job of ours, running or waiting in cmp-run.sh's line, holds a slot of the kind
its scratch is. As a line is taken (`ht1-queue.sh`, `place`):

1. a RAM slot is free and its D fits the RAM disk right now (`cmp-run.sh
   --free`'s "after" room): it works in the RAM disk;
2. else an SSD slot is free and `/bkoscratch` has room for its D
   (`--ssd-free`): it works there, though the RAM disk may have room when it
   is the RAM slots that are taken;
3. else it is not started now. It stays in the todo, for the other hosts or
   for later here; the runner skips it this time and takes the next line that
   can start.

A line whose D is over the host's RAM-disk ceiling takes an SSD slot only, a
`+img` line a RAM slot only. Memory is needed wherever the scratch is, so a
line with a free slot whose memory alone is short starts and waits for it in
cmp-run.sh's line, as every HoltTest job always has (the queue keeps its place
there beside SticTest's and GuestTest's jobs); so does a line with room nowhere
when no job of ours is on the host. The modes `ram` and `ssd` count no slots
(`holt_jobs` alone); `overflow` with 0 SSD slots is `ram`, with 0 RAM slots
`ssd`. `hosts.py holt-slots HOST` prints what the runner reads, `MODE RAM SSD`.

The big guests' turns (`BIG_GIB`: a line of 13 GiB or more one at a time per
host) are not kept in mode `overflow`: the slots replace them. The turns never
guarded the RAM disk; the ledger does (a `--reserve D M` is admitted only when
D fits beside every other reservation and what the tmpfs holds). They kept a
second big line from sitting in cmp-run.sh's line, where it held up every other
start of the runner's. In mode `overflow` a line enters a RAM slot only when
its D fits right now by those same figures, so two big lines never wait on each
other, and the RAM slots say how many jobs work in the RAM disk at once. Mode
`ram`, where a line still waits for its room, keeps the turns.

A `+img` line is never moved (ht1.sh runs it only in the RAM disk). A line on
`/bkoscratch` runs as ht1.sh's disk mode always has (`HT_RAM=0`: it works in its
records directory, a pass deletes the bulk and keeps the records, a failure
keeps everything in place), under `cmp-run.sh --reserve-ssd D M`: its M counts
against the memory as any job's, and its D against the SSD's room, the free
space of `/bkoscratch` above `ssd_floor_gib` (80) less what the other SSD jobs
hold. A host short of free SSD gets no overflow: its lines stay in the RAM disk.
`cmp-run.sh --ssd-free` prints the room. The ledger row is `PID 0 M - reserve
ssd=D CMD`, which an older reader takes as a job with no RAM disk.

With scratch off the RAM disk, memory is the limit, so `holt_jobs` rose as far
as the memory pool carried then (MemTotal less the whole tmpfs, the ARC's GiB and
`base_gib`; a HoltTest job reserved 4 GiB of it, and SticTest and GuestTest
share it):

| host | mode | `holt_jobs` | memory pool (ceiling) | threads | `/bkoscratch` free, 2026-11-23 | SSD room |
|---|---|---|---|---|---|---|
| chopin | overflow | 4 (was 3) | 25.2 GiB (23.2) | 12 | 347 GiB | 267 |
| cinqcent | overflow | 4 (was 3) | 25.2 GiB (23.2) | 12 | 264 GiB (sda2, SATA) | 184 |
| copland, cage, czerny | overflow | 2 (was 1) | 12.1 GiB (10.1) | 16 | 236, 221, 354 GiB | 156, 141, 274 |
| choubox | overflow | 1 | 8.0 GiB (6.0) | 4 | 95 GiB | 15 |
| bigfoot | ram | 1 | | | | none |

Four, not five or six, on the big hosts: five jobs would leave 5 GiB of the pool
for everything else, and Cinqcent was held for swap-out the day this was set.
Two on the M900s: three would take the whole pool. Cage is the host to watch:
its SN770 has dropped off PCIe under heavy I/O.

Since 2026-11-24 the pool no longer loses the whole tmpfs: it is MemTotal less
the ARC's GiB, `base_gib` and the RAM disk reserved (or written), and each job
takes its D + M of it, so a job on the SSD (D 0) or with a small RAM disk
leaves the rest of the RAM disk's memory to the others. With the RAM disk
empty that is 55.2 GiB on the big hosts, 22.1 on the M900s and 12.0 on
Choubox (against 25.2, 12.1 and 8.0 before); with the RAM disk all reserved it
is what it was. The live check (MemAvailable less what reservations may still
write and use, swap in use and 2 GiB) still guards it. The reservations were
trimmed the same day: SticTest's M is the guest's RAM + 1 GiB (mostly 6 to 5;
stictest1/README.md), and a HoltTest line's M from its peaks is 1.10 x its anon
+ 0.25 GiB to a tenth, at least 3.5 (ram-size.sh; about 3.6, was 4). The slots
(`stic_slots`, `holt_jobs`) are as they were: what the pool gained lets them be
filled beside one another (on an M900, two SticTest checks and a HoltTest job
on the SSD: 2 x (3 + 5) + 3.6 = 19.6 of 22.1, where before 2 x 6 + 4 was over
its 12.1), not more of them. Watch the swap rule's holds the first rounds after
a sync: if they grow on a host, raise its `base_gib`.

Test2's write stop rule leaves an SSD job's bytes out (its wear line says
`scratch=ssd`; a RAM-disk job's says `scratch=ram` and is held to the budget as
before); the report's totals still count them.

To put a host back: `"scratch_mode": "ram"` in its row of `testvm/hosts.json`
(every host: in both `class_limits`), and `holt_jobs` back to 3 (big) or 1
(small) beside it, then `testvm/sync-hosts.sh` when no run is live. The next
run's runners read the table as they start.

After a cycle, `t2 pull`, then `testvm/test2/host-rates.py --since UTC` prints
per host and family the cells an hour, the jobs at once, the median wall and
the GB written, with HoltTest's RAM and SSD jobs on lines of their own and, per
host, the cells that passed both ways (the SSD wall against the RAM wall).
`--cut UTC` puts the rows before and since a time side by side. On a host,
`testvm/compare/wear-report.py --tag ht1` sums the wear log by tag.

**Placing work by hand: always ask, never name two hosts.** Every session that
starts its own job (`ht1.sh`, `make-*.sh`, a guest boot, a compare run) asks
`testvm/pick-host` for the host, with the job's `--reserve` and guest, and runs
it there. "Both hosts" (Chopin and Cinqcent) is not the default, and a PASS on
"both hosts" is not the done criterion: a job passes on the host pick-host
gave it. A sync names all six amd64 hosts (`testvm/sync-hosts.sh
m@chopin.local m@cinqcent.local m@cage.local m@copland.local m@czerny.local
m@choubox.local`), never two. Why: docs/design/four-host-split.md.

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
  cachyos-zfs, chimera-zfs, debian13-zfs, proxmox9, proxmox9-mirror, truenas25,
  ubuntu24, ubuntu26-zfs, ubuntu26-zfsmirror, void-zbm, void-zbm-enc
  (truenas25-mirror is no HoltTest2 source: HoltTest1 1D runs it). This
  applies to every variant, `+img` included. Every amd64 host runs OpenZFS
  2.4.4 today, so this keeps no cell off a host of the farm.
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

**What stays on the big hosts**. The small hosts are default hosts now, so
each of these is decided per cell, from the host's facts, not from its name:

- guests that are not published to the store;
- the host-ZFS holt sources;
- bko-holt and image builds (the big build hosts; a round's build units are dealt over all four since 2026-11-14);
- cells over a small host's ceiling (9.0 10.1, and Choubox's 3.0 6.0): for example stic debian13-zfs
  (`--reserve 13 5`) and
  ubuntu26-zfsmirror (23 GiB). The gate's per-host loop cells are the
  exception: they run on a small host too, with that class's smaller
  `host_reserve` (`loop:linux` 8.5 4, `loop:sparse` 6 4);
- the holt pins in `classes.json`: openbsd8 on Chopin, and dragonfly6 and redox
  on Cinqcent. The redox tools are also on Cage and Copland, but not on Chopin.
- UEFI holt sources, which run only where `rescuer-uefi.qcow2` exists (Chopin,
  Cinqcent, Copland and Cage have it, checked 2026-11-14; placement still goes
  by the host's facts, Choubox's too);
- the fork gates (`fork:*` pinned in `gate/layers.json`);
- run-all.sh's guest platforms in its profiles: on a small host its own
  profile runs only linux and sparse. A `SPLIT` round deals every step,
  guest platforms and ZFS's included, by the hosts' capabilities from their
  facts (`SPLIT_CAPS`, testvm/test2/split-caps.py; img-speed.md F6), so the
  small hosts take their share.
- vm-update's fleet, the store mirror and the Bigfoot link (all Chopin).

**Ties** (docs/design/img-speed.md F13, 2026-11-26). Where a planner rates two
hosts the same (t2's estimates, the gate's fallback host, ImgTest2's target,
drive.py's simulation, run-all.sh's SPLIT deal, bs2's deals), the tie goes by
class (big first), then `heat` (the cooler first), then the table's order:
`hosts.py tie_key`, `hosts.py names --ties`. `heat` is a limit, 0 to 9, default
0; Chopin's is 1 (its cooler), so a tie between the big hosts goes to Cinqcent.
The table's order is unchanged and still what lists and reports keep.

**Heat limits** (docs/design/img-speed.md F10, 2026-11-27). `hot_max` (whole
degrees C, or null: none) makes heavy work on a host wait while sensors' Tctl
is over it (rustic-exact-round.sh, before each job); `xz_threads` (default 4)
is compress-images.sh's `xz -T` there. Chopin's are 95 and 2, its cooler: both
were checks of Chopin's name in those scripts. The audit and compare scripts
(rust-lint.sh, stock-baseline.sh, rustic-exact-round.sh) take their hosts from
the table and pick-host, never a name of their own.

## Bigfoot, the arm64 host

Bigfoot (`m@bigfoot.local`, 192.168.68.197 as its `ip`) is the user's daily
machine: a desktop session, Plex and deluge run there. Its row is small class,
`arch: arm64`, `job_slice: bko.slice`, with the one role `build-arm64` (A2)
and `default: false`. It
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
- **Builds (A2).** Its Rust is rustup's, with `RUSTUP_HOME` and `CARGO_HOME`
  in `/home/m/bko-arm64/toolchains` (the row's `rustup_home` and
  `cargo_home`; `~/.rustup` and `~/.cargo`'s registry are the user's own and
  are never used). `build-round.sh --arch arm64` builds the round's
  rustic-bko `linux-arm64` there, natively, in `bko.slice`, through
  cargo-zigbuild (stick/zig-tools.sh's zig and cargo-zigbuild in
  `~/backotter-work/tools`), and `build-holt.sh linux-arm64` builds
  bko-holt there. Its sets hold only arm64 directories: `bko-bin/linux-arm64`,
  `holt-bin/linux-arm64` and a round's `bin/linux-arm64`.
- **A6's tests (docs/design/arm64.md, "What A6 built").** These are
  SticTest1 of the five arm64 guests, HoltTest1 1ARM and its `+img`,
  HostTest2 and the Test2 candidates. Run them all from the Mac with one
  command, `testvm/arm64/a6-run.sh` (`--dry-run` first, then `--status`,
  `--fetch` and `--stop`). The command:
  - ships committed HEAD to `~/backotter-work/a6-run/STAMP`
  - runs the stages there one at a time, in a transient service,
    `bko-a6-STAMP.service`, in bko.slice

  HostTest2 there is run-all.sh's arm64 profile: ZFS on files and tmpfs
  only, since Bigfoot gets no loop device or package.

## Adding a host

Czerny was added this way on 2026-11-19: its reserved row (3100-3199, ports
3180-3199, small) got `"enabled": true` and Cage's roles (`build`, `cache`),
and `default: true` later the same day, by the user's choice. For any other new
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
   - The storage tools every host has (all four alike since 2026-11-14), for
     vm-mount, the bs2 dry runs and the tier 0 chroots: `sudo apt-get install
     --no-install-recommends cryptsetup-bin lvm2 thin-provisioning-tools
     dmsetup mdadm xfsprogs xfsdump jfsutils btrfs-progs e2fsprogs f2fs-tools
     nilfs-tools dosfstools exfatprogs ntfs-3g udftools hfsprogs reiserfsprogs
     squashfs-tools erofs-utils gdisk parted fdisk kpartx qemu-utils mtools
     zfs-dkms zfsutils-linux` (ZFS from trixie contrib, built by DKMS against
     the running kernel's headers; check `modprobe zfs`). bcachefs-tools and
     ufsutils aren't packaged for trixie.
   - python3-virt-firmware (`virt-fw-vars`), for `vm-run --mok`: the guests
     whose `vm-run-options` enroll a MOK (anduinos2, solus49) don't boot
     without it. Choubox lacked it at its join (GuestTest2All
     t2v-20261117T1501-13fa: anduinos2 HARNESS in phase boot).
   - sbsigntool (`sbverify`), for HoltTest's verify phase: it holds those
     same guests' restored `grubx64.efi` to the source's MOK with it
     (`sudo apt-get install -y --no-install-recommends sbsigntool`). On a
     host without it ht1-queue.sh leaves their lines for the other hosts,
     and its start line says `sbverify no`. Choubox lacked it until
     2026-11-23 (HoltTest2 t2h-20261123T1801-d49a: anduinos2+reformat-ext4
     FAIL at "MOK signature", the first MOK line it ever took).
   - jq isn't needed.
   - `hostname -s` must print the row's name.
2. **SSH.**
   - The Mac to `m@NAME.local`, with BatchMode.
   - The new host to Chopin, for vm-store fetches, the holt queue host and
     vm-update's fleet lock.
   - Chopin and Cinqcent to the new host, for build-round.sh `--dist-hosts` and
     promote-bins.sh. Both copy host to host, and go through the Mac when they
     can't.
   - Between the small hosts too, and each host to itself (`m@NAME.local`).
     Cage and Copland reach each other and themselves since 2026-11-14.
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
     version. For comparison, Cage's and Copland's caps were raised from 400G to
     500G on 2026-11-19, at about 420G used (overlays of BackShot2 sources,
     kept failures and HoltTest runs hold many objects, so gc frees little).
   - Link each guest the host should have: `vm-store link GUEST...`. This
     fetches from Chopin and creates `vms/GUEST`.
   - After this, every publish on Chopin relinks the host itself (it is
     found by its `cache` role here). `vm-store status --farm` on Chopin
     shows a host that is behind, and `vm-store relink-caches --hosts NAME`
     catches it up. See `testvm/store/README.md`, "Publish relinks the
     caches".
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
   chopin,cinqcent,copland,cage,czerny,choubox,NAME`, and the same for `HoltTest2All` and `GateTest2`.
   Each plan probes the new host's facts (guests, ceiling, caps) and writes
   nothing. Its reasons show which cells it keeps off the new host and why
   (`kept off NAME: ...`). On the host, check that
   `testvm/compare/cmp-run.sh --ceiling` prints the expected `DISK MEM`.
9. **Trial runs.** Run GateTest2 (the HostTest2 layers) and one representative
   per family with `--hosts` naming the new host. For a small host, these are
   the cells that fit it.
10. **Join the defaults.** Set `"default": true` in hosts.json, then update
    what counts the default hosts: `tests/test_hosts.py`,
    `tests/test_gate_hosts.py`, `tests/test_host_table.py`,
    `tests/test_gate.py`'s universe, `tests/test_bko.py`'s sweep,
    `tests/test_holt.py`'s fake hosts, t2's usage, drive.py's self-test, and
    the `fixtures/p2/golden` files (`T2_GOLDEN=bless` in a whole
    `python3 -m unittest discover -s tests` run rewrites them). This is a code
    change. Then the prose that counts the hosts by name: hosts.json's
    `notes`, this page (the table, the host's paragraph, the sync line),
    `t2lib/__init__.py`'s host comment, testvm/test2/README.md,
    testvm/stictest1/README.md and testvm/compare/README.md. A shell script
    reads the defaults from the table (`hosts_names --default --arch amd64`,
    as ht1-farm.sh and rustic-exact-round.sh do), never a list of its own.
    Only then do t2, drive.py and SweepTest2's completeness check
    include the host without `--hosts`. vm-update's fleet stays the
    hosts with the fleet role.

The join rule is in `docs/design/test2/design-classes.md` §2.6.
