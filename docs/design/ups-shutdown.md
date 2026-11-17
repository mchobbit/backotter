# UPS-driven shutdowns (plan)

Status (2026-11-17): **applied on every machine.** UPS-A (GMKOne and
Bigfoot), UPS-B (GMKTwo) and UPS-C (GMKThree and all six of its
secondaries) are all set up. Still open: T4 for Bigfoot, after the disk
move and in a window the user picks, and the user's T5 pull-the-plug
tests. See "Applied so far" under the apply order. Each config change
left a dated `.bak-*` copy beside the file in `/etc/nut`.

## Goal (the user's checklist)

| UPS | Plugged into (USB) | On low battery it shuts down |
|-----|--------------------|------------------------------|
| UPS-A | GMKOne | Bigfoot, then GMKOne |
| UPS-B | GMKTwo | GMKTwo |
| UPS-C | GMKThree | Cinqcent, Chopin, Cage, Copland, Coventry, Choubox, then GMKThree |

## What exists today (surveyed read-only 2026-11-15, ~20:55)

| Machine | HW / OS / arch | NUT today | Notes |
|---|---|---|---|
| GMKOne 192.168.68.191 | GMKtec NucBox G5, Debian forky/sid, amd64, kernel 6.12.30 | nut 2.8.4, **standalone**, `[eaton]` usbhid-ups, upsmon primary, listens 127.0.0.1 | Eaton 3S 700 on USB, OL CHRG, 123 W (22 %), ~24 min runtime. GmkOnePool on 5 VL817 USB-SATA bridges. |
| GMKTwo .192 | NucBox G5, Debian 13, amd64, 6.12.107 | nut 2.8.1. **Was** MODE=none with a stale Greencell `nutdrv_qx` config, driver crash-looping, i.e. **unprotected**. Fixed in step 0: now standalone, `[eaton]` usbhid-ups, primary | Eaton 3S 700, OL, 78 W (14 %), ~34 min. GmkTwoPool on a JMS578 bridge. |
| GMKThree .193 | NucBox G5, Debian 13, amd64, 6.12.107 | Same as GMKTwo: was unprotected (Greencell config), fixed in step 0 | Eaton 3S 700, OL, **179 W (32 %), ~15 min**. GmkThreePool on a JMS578 bridge. |
| Bigfoot .197 | Minisforum MS-R1, Debian 13, **arm64**, 6.19.10 | nut 2.8.1, **netserver** for `[eaton]`, but **no UPS on its USB**: driver crash-loops; upsd listens on 192.168.68.72, a stale address | DantePool (6x20 TB raidz2 on ASMedia UAS bridges, already flaky). Runs deluge, smbd/nmbd, NFS server, vnc, openvpn, miniircd, hugos-heartbeat. |
| Coventry .190 | NanoPi M6, Ubuntu 24.04, **arm64**, vendor 6.1 | nut 2.8.1, was the netserver for `[eaton]` (with Choubox as its secondary) | **No UPS on its USB** (the user confirmed: "Coventry has no UPS of its own"); driver crash-loops. `m` has **no sudo** here, but `root@coventry.local` takes the key. No RTC: its clock and `last` are unreliable. |
| Choubox .195 | GMKtec NucBox G3 Pro, Debian 13, amd64 | nut-client only, **netclient**: `MONITOR eaton@coventry ... secondary` | **Still exists** and is up, though it left the bko farm. Its monitor watches Coventry's dead driver, so it is unprotected. |
| Chopin .194, Cinqcent .196 | MoreFine S500+, Debian 13, amd64, 7.2 | **none installed** | bko KVM hosts, 12 threads / 62 GB, tests running under `cmp-run.sh --reserve` scopes (`run-p*-i*.scope`). WoL supported, **off** (`Wake-on: d`). |
| Cage .199, Copland .198 | Lenovo M900, Debian 13, amd64, 7.2 | **none installed** | Same as above, 16 threads / 27 GB. |

The `*.bak-eaton` files dated 2026-07-31 and 2026-08-02 on GMKOne, Bigfoot and
Coventry show an Eaton 3S was on Bigfoot and on Coventry before. The three
Eatons now sit on the three GMKs.

**ssh (BatchMode, as `m`) between the hosts:** the four farm hosts reach each
other; Chopin reaches Bigfoot; GMKOne and GMKTwo reach each other and GMKThree;
Choubox and Bigfoot reach each other. **No GMK reaches any other host, and
nothing reaches Coventry.** No GMK runs a firewall (ufw inactive, no nft
input chain), so NUT's TCP 3493 is open once upsd listens on the LAN.
Every host resolves `*.local`.

**Outlets (confirmed by the user 2026-11-15):** UPS-A feeds GMKOne and
Bigfoot. UPS-B feeds GMKTwo. UPS-C feeds GMKThree, Cinqcent, Chopin, Cage,
Copland, Coventry and Choubox. So UPS-C's 179 W covers seven machines.

**Still unknown:** each machine's BIOS "restore on AC power loss" setting.
The OS can't read it, so the user checks it.

## Decisions

**D1. GMKThree is the master for UPS-C, and Coventry becomes its
secondary.** The Eaton is on GMKThree's USB, and Coventry has none, so
there's no real conflict: Coventry's master role is a leftover. Coventry
moves from netserver to netclient (`MONITOR eaton@gmkthree.local`). This
needs root on Coventry. `m` has no sudo there, but `root@coventry.local`
takes the key, and the change was applied that way on 2026-11-17. Bigfoot's own leftover netserver goes the same way and becomes
a netclient of GMKOne.

**D2. Secondaries use NUT (`upsmon` netclient), not ssh pushed from the
master.** The GMKs can't ssh anywhere today, and Coventry takes no ssh at
all. A NUT secondary pulls status itself, so it needs no keys and no root
access from the GMKs. If the master dies while on battery, the secondary
treats "last seen OB, now unreachable" as critical and shuts down on its own.
Choubox already works this way. Farm hosts install `nut-client` only.

**D3. Triggers. Every host waits for low battery, and the master sets the
threshold with headroom.** The user decided this on 2026-11-16: the farm
gets no early on-battery timer.
- On each master, in ups.conf: `ignorelb` plus
  `override.battery.charge.low` and `override.battery.runtime.low`, so NUT
  raises LB at our threshold, not the Eaton's 20 %.
  - UPS-A: charge 40 %, runtime 600 s. Stopping Bigfoot's services and
    flushing DantePool over flaky USB bridges needs minutes, not seconds.
  - UPS-B: charge 30 %, runtime 300 s.
  - UPS-C: charge 50 %, runtime 600 s. UPS-C carries seven machines, about
    15 min at 179 W (measured 2026-11-15 with all four farm hosts running
    tests). The Eaton's runtime estimate falls faster than linearly under
    heavy load. The farm's halt (up to 45 s for test scopes, plus about 90 s
    of systemd shutdown) and GMKThree's wait for its secondaries (up to
    180 s) must all fit inside the threshold, so it gets more headroom
    than UPS-A.
- No host uses an on-battery timer, so a short blip takes nothing down. The
  order inside one UPS comes from D4 (the master halts last), not from
  timers.

**D4. The master halts last and cuts power only after its secondaries are
dark.** On FSD, NUT's primary waits only until the secondaries' upsmon
*disconnects* (HOSTSYNC), which happens early in their shutdown. Then it
halts, and the UPS cuts output `offdelay` seconds later, which could catch
Bigfoot mid-shutdown. So each master's SHUTDOWNCMD is a wrapper,
`/usr/local/sbin/ups-primary-halt`. It pings its secondaries until none
answer, for at most 300 s (UPS-A) or 180 s (UPS-C), then runs
`systemctl poweroff`. NUT's usual POWERDOWNFLAG (`/etc/killpower`) plus
Debian's `nutshutdown` system-shutdown hook then tell the UPS to cut and
later restore power, so every machine sees a clean power cycle and boots.

## Per-UPS design

### UPS-A: master GMKOne, secondary Bigfoot

- **GMKOne:**
  - nut.conf `MODE=netserver`.
  - upsd.conf `LISTEN 0.0.0.0 3493` **on its own**. A `LISTEN 127.0.0.1` line
    before it takes the port first, and the 0.0.0.0 bind then fails. 0.0.0.0
    already covers localhost and doesn't depend on the DHCP address. (`LISTEN ::`
    is v6-only in NUT and refuses IPv4 clients.)
  - upsd.users: add `[upssecondary] upsmon secondary` with its own password.
  - ups.conf `[eaton]` adds `ignorelb`, `override.battery.charge.low = 40`,
    `override.battery.runtime.low = 600`, `offdelay = 60` and
    `ondelay = 120`.
  - upsmon.conf: `SHUTDOWNCMD "/usr/local/sbin/ups-primary-halt bigfoot.local"`
    and `HOSTSYNC 30`.
- **Bigfoot:**
  - nut.conf `MODE=netclient`.
  - Disable nut-driver@eaton and nut-server. Remove the `[eaton]` section and
    the stale `LISTEN 192.168.68.72`.
  - upsmon.conf: `MONITOR eaton@gmkone.local 1 upssecondary <pw> secondary`
    and `SHUTDOWNCMD "/usr/local/sbin/bigfoot-ups-halt"`.
- **`bigfoot-ups-halt`:**
  1. Log to syslog.
  2. Stop `deluged deluge-web smbd nmbd nfs-server vncserver@:1`, the
     services that write to DantePool, each under a 60 s timeout.
  3. Run `systemctl poweroff`.
- **No `zpool export` (decided by the user 2026-11-16).** ZFS stays
  consistent across a normal poweroff. An export only marks a pool free for
  another host, and it would drop DantePool from `/etc/zfs/zpool.cache`, so
  the stock boot import would skip it. It can also hang on a stalled USB
  bridge while the battery drains.
- **Re-import at boot:** the stock `zfs-import-cache.service` (enabled, with
  DantePool in the cache file) does it. On 2026-11-15 the six disks attached
  at 4.3 s after boot, the import started at 5.6 s and finished at 18.5 s,
  and zfs-mount followed 1 s later. Nothing new is needed.
  - **Optional, only if the boot after the disk move shows the new
    housings arriving late:** a drop-in for `zfs-import-cache.service`
    whose ExecStartPre waits up to 180 s for the six `/dev/disk/by-id`
    members.
- **Timing:** apply Bigfoot's part only **after the disks move to the new
  2-bay housings (week of 2026-11-16)**, and test its shutdown only in a
  window the user picks. The pool must not be disturbed before then.
- **Order:** Bigfoot stops its services and powers off. Then GMKOne waits
  until Bigfoot stops answering ping (at most 300 s) and halts, and the UPS
  cuts output 60 s later.

### UPS-B: master GMKTwo, no secondaries

- It stays standalone as configured in step 0. Add `ignorelb`,
  `override.battery.charge.low = 30`, `override.battery.runtime.low = 300`,
  `offdelay = 60` and `ondelay = 120`. SHUTDOWNCMD stays
  `/sbin/shutdown -h +0`.
- GmkTwoPool: a normal systemd shutdown unmounts it and leaves it clean, and
  it re-imports from the cache at boot.

### UPS-C: master GMKThree, secondaries the farm, Coventry and Choubox

- **GMKThree:**
  - nut.conf `MODE=netserver`, upsd `LISTEN 0.0.0.0 3493` alone (see UPS-A), and add
    `[upssecondary]` to upsd.users.
  - ups.conf adds `ignorelb`, charge 50 %, runtime 600 s, `offdelay = 60`
    and `ondelay = 120`.
  - upsmon.conf: `SHUTDOWNCMD "/usr/local/sbin/ups-primary-halt chopin.local cinqcent.local cage.local copland.local coventry.local choubox.local"`
    (180 s cap) and `HOSTSYNC 30`.
- **Chopin, Cinqcent, Cage and Copland:**
  - Install `nut-client` and set `MODE=netclient`.
  - upsmon.conf: `MONITOR eaton@gmkthree.local 1 upssecondary <pw> secondary`,
    and `SHUTDOWNCMD "/usr/local/sbin/bko-ups-farm-halt"`. There is no
    upssched timer: the farm waits for LB like everyone else (D3).
- **`bko-ups-farm-halt`** (in the repo under `testvm/ups/`, synced like the
  other farm tools):
  1. Log `UPS: farm halt` to syslog and to the wear log.
  2. `touch` `~m/backotter-work/cmp-logs/*/stop`, cmp-run.sh's own way to
     tell workers to start nothing more.
  3. `systemctl kill --signal=TERM` every `run-p*-i*.scope` (the
     `--reserve` scopes; never pkill by name or user), then wait up to 45 s
     for them to empty. QEMU exits on SIGTERM, and the guests' disks are
     overlays in RAM or scratch, so they don't need a clean guest shutdown.
  4. `systemctl poweroff`. systemd stops whatever is left.
  - `--dry-run` prints the scopes and queues it would touch, and changes
    nothing.
  - The `stop` files survive the reboot on purpose: queues stay stopped
    until someone restarts them. The batch runner or the session that owns a
    run sees FAIL or timeouts, as with any host loss.
- **Choubox:** keep its netclient, point `MONITOR` at `eaton@gmkthree.local`
  with the `upssecondary` user, and keep `SHUTDOWNCMD`.
- **Coventry** (as root over ssh; applied 2026-11-17):
  1. `MODE=netclient`.
  2. Disable nut-driver@eaton and nut-server, and drop the `[eaton]`
     section.
  3. upsmon.conf: `MONITOR eaton@gmkthree.local 1 upssecondary <pw> secondary`
     and `SHUTDOWNCMD "/sbin/shutdown -h +0"`.
- **Order:**
  1. On LB, GMKThree sets FSD.
  2. All six secondaries halt at once. The KVM hosts stop their test scopes
     first.
  3. GMKThree waits until all six stop answering ping (at most 180 s), then
     halts.
  4. The UPS cuts output 60 s later.

## No pool exports

Decided by the user on 2026-11-16: no UPS shutdown exports a ZFS pool.
That covers DantePool (Bigfoot), GmkOnePool (on five VL817 USB-SATA
bridges), GmkTwoPool and GmkThreePool (each on a JMS578 bridge).

- Each host shuts down with a normal `systemctl poweroff`, which leaves its
  pool consistent and still listed in `/etc/zfs/zpool.cache`.
- At boot, the stock `zfs-import-cache.service` imports it.
- The masters' SHUTDOWNCMD (`ups-primary-halt`) never runs `zpool export`.
- If a bridge turns out to come up after the import runs, the fix is the
  optional wait-for-disks drop-in described under UPS-A, applied to that
  host. Still no export.

## Power restore and boot order

- When mains returns, the Eaton waits for `ondelay` (120 s) and the battery,
  then powers its outlets. Every machine on it gets AC at the same time.
- Each machine needs BIOS **"Restore on AC power loss: Power On"**. The user
  checks this on the GMKtec G5 and G3 Pro, MoreFine S500+, Lenovo M900 and
  MS-R1. The NanoPi powers on by design.
- **If mains returns during the shutdown**, killpower still cycles the
  outlets, so everyone boots. Without killpower the machines would stay off.
- **Boot order:** nothing needs another host to boot. Secondaries whose
  master is still booting retry quietly (upsmon's NOCOMMWARNTIME).
  Bigfoot and the GMKs re-import their pools from the cache file (see
  "No pool exports"). Farm hosts come up with
  empty RAM disks and stopped queues.
- **Optional fallback:** enable WoL (`ethtool -s eno1 wol g`, kept by a
  systemd .link file) on the farm hosts. A GMKThree timer then sends a magic
  packet to any host that hasn't answered 10 min after it boots. This covers
  a BIOS that won't power on after AC loss.

## Testing, safest first (each step needs the user's go-ahead)

- **T0. Done 2026-11-15.** All three masters report `OL` through
  `upsc eaton@localhost`, and upsmon is logged in.
- **T1. Simulated, in QEMU guests on a farm host** (normal `cmp-run.sh
  --reserve`):
  1. Run two Debian 13 guests: "master" with `dummy-ups`
     (`port = sim.seq`) and the D4 wrapper, and "secondary" with the farm
     and Bigfoot scripts, with stub services and a file-vdev zpool standing
     in for DantePool.
  2. Walk the `.seq` file through OL to OB (check that nothing halts on OB
     alone), then OB LB.
  3. Run `upsmon -c fsd` on the master.
  4. Check the order and the ping wait, and that the pool comes back from
     the cache file on the next boot. Real shutdowns, nothing real at risk.
- **T2. Scripts' dry runs on the real hosts:**
  - `bko-ups-farm-halt --dry-run` on each farm host.
  - `bigfoot-ups-halt --dry-run` on Bigfoot (lists the services it
    would stop, runs nothing).
  - `ups-primary-halt --dry-run HOSTS` on each master (pings once, reports,
    doesn't halt).
- **T3. Notify path on the real hosts, no shutdown:**
  1. Temporarily set each secondary's `SHUTDOWNCMD` to
     `logger UPS-TEST would halt`.
  2. On the master, swap in a `dummy-ups` `[sim]` for the real `[eaton]`
     under the same name, so secondaries see "eaton" go OB, and remove
     `POWERDOWNFLAG` from the master's upsmon.conf. With the flag present, a
     test FSD would write `/etc/killpower` and the next ordinary reboot would
     cut the UPS.
  3. Run `upsmon -c fsd` on the master.
  4. Check that every secondary logged UPS-TEST.
  5. Put the configs back, delete any `/etc/killpower`, and restart
     nut-monitor everywhere.
- **T4. One real host at a time.** On a secondary, `upsmon -c fsd` shuts
  down only that host. Do it on a farm host with no reservations
  (`cmp-run.sh --free`, `t2 batch status`), then Choubox, then Coventry. Do
  Bigfoot last, after the disk move and in the user's window. Check that each
  boots and rejoins.
- **T5. Pull the plug. Only the user does this**, one UPS at a time, farm
  idle, watching `upsc` from another machine. It's the only test that
  exercises killpower, the BIOS power-on and the USB timing of a real power
  cycle.

## Apply order (each step needs the user's OK)

0. **Done 2026-11-15:** GMKTwo and GMKThree were switched from the stale
   Greencell config to `[eaton]` usbhid-ups, standalone. nut-driver@eaton,
   nut-server and nut-monitor are enabled and running, and nut-driver@greencell
   is disabled. Backups are `/etc/nut/{nut.conf,ups.conf,upsmon.conf}.bak-greencell-20261115`.
1. Write `testvm/ups/` (the three scripts), then run T1.
2. UPS-B thresholds (GMKTwo alone).
3. UPS-C: GMKThree netserver, then the farm hosts (install nut-client), then
   Choubox, then Coventry (as root over ssh, done 2026-11-17). Then T2, T3 and T4.
4. UPS-A: GMKOne netserver, then Bigfoot (both done; Bigfoot joined 2026-11-17, before the disk move). Then T2, T3
   and T4 for Bigfoot.
5. T5, the user's pull-the-plug tests.

### Applied so far (2026-11-16, morning)

- **UPS-B, done.** GMKTwo's `[eaton]` has `ignorelb`, charge.low 30,
  runtime.low 300, offdelay 60 and ondelay 120. `upsc` shows
  `battery.charge.low: 30`, `battery.runtime.low: 300`,
  `ups.delay.shutdown: 60` and `ups.delay.start: 120`. Status OL.
- **UPS-C, master.** GMKThree has:
  - `MODE=netserver`, `LISTEN 0.0.0.0 3493`, and the `[upssecondary]` user
    (random password, kept only in `/etc/nut` on each host);
  - `ignorelb`, charge.low 50, runtime.low 600, offdelay 60 and ondelay 120;
  - `HOSTSYNC 30`.
  - `SHUTDOWNCMD "UPS_WAIT_MAX=180 /usr/local/sbin/ups-primary-halt chopin.local cinqcent.local cage.local copland.local coventry.local choubox.local"`,
    installed after T1 and T2 passed.
- **UPS-C, secondaries.**
  - Chopin, Cinqcent, Cage and Copland have `nut-client` installed from
    trixie and `MODE=netclient`, with
    `MONITOR eaton@gmkthree.local 1 upssecondary <pw> secondary`.
    `SHUTDOWNCMD "/usr/local/sbin/bko-ups-farm-halt"`, installed in
    /usr/local/sbin.
  - Choubox's MONITOR now points at `eaton@gmkthree.local` with the
    `upssecondary` user.
  - All five show as logged in on GMKThree (`upsc -c`).
- **UPS-C, Coventry: done 2026-11-17** (as root over ssh; `root@coventry.local`
  takes the key, though `m` has no sudo there).
  - `MODE=netclient`; nut-driver@eaton and nut-server disabled and
    stopped.
  - ups.conf has no sections; the old `[eaton]` is in
    `ups.conf.bak-netclient-20261117`.
  - `MONITOR eaton@gmkthree.local 1 upssecondary <pw> secondary` and
    `SHUTDOWNCMD "/sbin/shutdown -h +0"`.
  - It reads OL from GMKThree, and GMKThree lists all six secondaries
    (`upsc -c`).
- **T1 PASS (2026-11-16, on Copland, two 1 GB guests, `testvm/ups/t1-rig.sh`):**
  - OB alone for 70 s halted nothing.
  - `ignorelb` dropped the UPS's own LB flag.
  - Charge below the override raised LB, then FSD. The secondary
    stopped the test scopes in 15 s and powered off. The master saw
    it dark after 41 s and powered off.
  - `upsmon -c fsd` hit the 60 s cap and logged who was still up.
  - Runtime below the override ran `bigfoot-ups-halt`: services stopped,
    no export, and the next boot re-imported the file-vdev pool from
    the cache.
  - Found along the way: a secondary logs out of upsd before running its
    SHUTDOWNCMD, so HOSTSYNC ends almost at once. The ping wait (D4) is
    what holds the master back.
  - `bko-ups-farm-halt` also stops `bko-*.scope`, cmp-run.sh's
    queue-job scopes.
  - Not covered (left to T5): killpower, the nutshutdown hook,
    offdelay/ondelay.
- **T2 PASS (2026-11-16):** the dry runs on the real hosts listed the right
  queues and scopes on all four farm hosts. GMKThree saw all six
  secondaries up, and Bigfoot listed its six services with no export.
  The scripts were installed afterwards on GMKThree and the four farm
  hosts. Their installed `--dry-run`s are clean, and all six clients are
  logged in again.
- **UPS-A, master (2026-11-16).** GMKOne has:
  - `MODE=netserver`, `LISTEN 0.0.0.0 3493`, and its own `[upssecondary]`
    user;
  - `ignorelb`, charge.low 40, runtime.low 600, offdelay 60 and ondelay 120
    (`upsc` confirms all of them);
  - `HOSTSYNC 30`;
  - `SHUTDOWNCMD "UPS_WAIT_MAX=300 /usr/local/sbin/ups-primary-halt bigfoot.local"`.
  - The dry run sees Bigfoot up, Bigfoot can already read
    `eaton@gmkone.local` (OL), and the UPS is OL.
- **UPS-A, Bigfoot: joined 2026-11-17** at the user's request, before the
  disk move. Joining touches only NUT, not the pool. Bigfoot and its disks
  are on UPS-A (the user).
  - `bigfoot-ups-halt` installed in /usr/local/sbin; its `--dry-run` lists
    deluged, deluge-web, smbd, nmbd, nfs-server and vncserver@:1, and no
    export.
  - `MODE=netclient`; nut-driver@eaton and nut-server disabled and stopped.
    ups.conf and upsd.conf are emptied, with the old `[eaton]` and the stale
    `LISTEN 192.168.68.72` kept in `*.bak-netclient-20261117`.
  - `MONITOR eaton@gmkone.local 1 upssecondary <pw> secondary` and
    `SHUTDOWNCMD "/usr/local/sbin/bigfoot-ups-halt"`.
    `NOTIFYCMD /usr/sbin/upssched` stays, with the stock upssched.conf: no
    AT lines, so no timers.
  - It reads OL from GMKOne, and GMKOne lists it as a client (`upsc -c`).
  - Its real shutdown test (T4) waits for the disk move and a window the
    user picks.

## Open questions for the user

1. ~~Confirm the outlets.~~ Answered 2026-11-15: as the checklist says.
2. ~~Should the farm halt on a 60 s on-battery timer?~~ Answered 2026-11-16:
   no, wait for low battery.
3. Should Choubox stay on UPS-C, or is it being retired?
4. ~~Who applies Coventry's change?~~ Done 2026-11-17 as root over ssh.
5. UPS-C's load under full test load: watch `upsc eaton@gmkthree.local
   ups.load` during a busy round. Over ~80 %, the farm should move onto a
   second UPS.
