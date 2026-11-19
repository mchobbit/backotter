# UPS shutdown scripts

NUT's `upsmon` runs one of these as its `SHUTDOWNCMD` when a UPS reaches
low battery (or is told FSD). The plan behind them, and which machine sits
on which UPS, is in `docs/design/ups-shutdown.md`.

| script | runs on | what it does |
|---|---|---|
| `ups-primary-halt HOST...` | each UPS's master (GMKOne, GMKThree) | pings each HOST every 5 s until none answers, or for at most `UPS_WAIT_MAX` seconds (default 300), logs who was still up, then `systemctl poweroff` (D4) |
| `bko-ups-farm-halt` | chopin, cinqcent, cage, copland, czerny | touches `~m/backotter-work/cmp-logs/*/stop`, SIGTERMs every `run-p*-i*.scope` and `bko-*.scope` (cmp-run.sh's), waits up to 45 s for them, then `systemctl poweroff` |
| `bigfoot-ups-halt` | Bigfoot | stops deluged, deluge-web, smbd, nmbd, nfs-server and vncserver@:1, each under a 60 s timeout, then `systemctl poweroff`. No `zpool export`, ever |

Each logs to syslog with tag `ups` (`journalctl -t ups`) and to stderr, and
powers the host off even when an earlier step fails. Each takes `--dry-run`:
it prints what it would do (and `ups-primary-halt` pings each host once) and
changes nothing, not even syslog. `--help` prints the header.

upsmon runs `SHUTDOWNCMD` as root through `/bin/sh -c`, even with
`RUN_AS_USER nut`, so an environment setting can go in front of the command.

## Install

As root on the host, from a checkout or `~m/backotter-work/testvm/ups`:

    install -m 755 -o root -g root ups-primary-halt   /usr/local/sbin/   # masters
    install -m 755 -o root -g root bko-ups-farm-halt  /usr/local/sbin/   # farm hosts
    install -m 755 -o root -g root bigfoot-ups-halt   /usr/local/sbin/   # Bigfoot

Then check it with `--dry-run` (test T2 in the plan).

## The NUT lines

The masters' `ups.conf`, in the `[eaton]` section (usbhid-ups):

    ignorelb
    override.battery.charge.low = 30      # all three UPSes
    override.battery.runtime.low = 420    # UPS-C; UPS-A 480, UPS-B 300
    offdelay = 60
    ondelay = 120

The masters' `nut.conf` has `MODE=netserver`, `upsd.conf` has the one line
`LISTEN 0.0.0.0 3493` (it covers 127.0.0.1 too), and `upsd.users` adds:

    [upssecondary]
        password = <pw>
        upsmon secondary

Don't keep a `LISTEN 127.0.0.1 3493` beside it: upsd binds that first, then
logs "not listening on 0.0.0.0 port 3493" and the secondaries get
"Connection refused" (found in T1, nut 2.8.1).

GMKOne's `upsmon.conf` (UPS-A):

    SHUTDOWNCMD "/usr/local/sbin/ups-primary-halt bigfoot.local"
    HOSTSYNC 30
    POWERDOWNFLAG /etc/killpower

GMKThree's `upsmon.conf` (UPS-C, a 180 s cap):

    SHUTDOWNCMD "env UPS_WAIT_MAX=180 /usr/local/sbin/ups-primary-halt chopin.local cinqcent.local cage.local copland.local czerny.local coventry.local choubox.local"
    HOSTSYNC 30
    POWERDOWNFLAG /etc/killpower

The farm hosts (`nut-client` only, `nut.conf` `MODE=netclient`):

    MONITOR eaton@gmkthree.local 1 upssecondary <pw> secondary
    SHUTDOWNCMD "/usr/local/sbin/bko-ups-farm-halt"

Bigfoot (`MODE=netclient`, after the disk move):

    MONITOR eaton@gmkone.local 1 upssecondary <pw> secondary
    SHUTDOWNCMD "/usr/local/sbin/bigfoot-ups-halt"

## Settings

| variable | script | default |
|---|---|---|
| `UPS_WAIT_MAX` | ups-primary-halt | 300 (seconds to wait for the secondaries) |
| `UPS_PING_EVERY` | ups-primary-halt | 5 (seconds between ping rounds) |
| `BKO_USER` | bko-ups-farm-halt | m (whose `backotter-work/cmp-logs`) |
| `UPS_SCOPES` | bko-ups-farm-halt | `run-p*-i*.scope bko-*.scope` |
| `UPS_SCOPE_WAIT` | bko-ups-farm-halt | 45 |
| `UPS_UNITS` | bigfoot-ups-halt | `deluged deluge-web smbd nmbd nfs-server vncserver@:1` |
| `UPS_STOP_WAIT` | bigfoot-ups-halt | 60 |

## Test

T1, the QEMU rehearsal, boots its two guests with `t1-rig.sh` (see its
header): M, debian13 with `nut-server` and `dummy-ups` on `/etc/nut/sim.dev`,
and S, debian13-zfs with `nut-client`, on a private socket LAN. Walk the UPS
by rewriting sim.dev (dummy-ups re-reads it when it changes). With
`ignorelb`, put `battery.charge` and `battery.runtime` **before**
`ups.status`: dummy-ups works out LB when it reads the status line, from the
figures it has by then, so a charge line after it takes effect only on the
next change. A device `LB` is dropped under `ignorelb`, as it should be.
dummy-ups doesn't cut power, so killpower (`/etc/killpower` is written) and
`offdelay`/`ondelay` are left to T5.
