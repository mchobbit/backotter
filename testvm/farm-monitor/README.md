# Farm monitor on Bigfoot

Bigfoot watches the build and test hosts (chopin, cinqcent, cage, copland,
czerny) live, so the next freeze leaves a record from outside the machine
that froze. Three pieces:

- **netconsole**: each host's kernel sends its console messages over UDP to
  Bigfoot as they are printed. A lockup, RCU stall, machine check, oops or
  panic gets out even when the disk and userspace are gone.
- **journal upload**: each host streams its whole journal to Bigfoot's
  `systemd-journal-remote`, so Bigfoot has the logs up to the last second,
  not up to the last flush to the host's own disk.
- **heartbeat**: Bigfoot pings each host and opens its port 22 every 15 s,
  and logs every change between up, ping-only and down, with times and how
  long the host was gone.

## Why

Cage froze twice on 2026-11-16, at 16:00 (idle) and at 22:13 (light load).
Its own journal just stopped, with no lines about it. The 60 s SP5100 TCO
hardware watchdog set up on 2026-11-13 did not reset it, and it stayed down
for 2 to 5 hours each time. The record has to be made somewhere else.

On 2026-11-17 the user chose this design over a reverse SSH tunnel from
each host to Bigfoot: netconsole and journal upload are push only, need
nothing on the hosts to stay up but the kernel and one daemon, and need no
keys. `testvm/telemetry/` (batches over SSH every 15 minutes, with a state
snapshot) complements it: that gives the state before a freeze, this gives
the last seconds.

## What goes where

| piece | on each host | on Bigfoot |
|---|---|---|
| journal | `systemd-journal-upload.service` (package `systemd-journal-remote`), `/etc/systemd/journal-upload.conf.d/bko-farm.conf` | `systemd-journal-remote.socket` on TCP 19532, plain HTTP; `/var/log/journal/remote/remote-IP.journal` |
| netconsole | `/usr/local/sbin/bko-netconsole`, `bko-netconsole.service` (boot) and `.timer` (every 10 min), `/etc/default/bko-netconsole` | `bko-farm-netconsole.service` on UDP 6666; `/var/log/bko-farm/netconsole/IP.log`, `by-name/NAME.log` |
| heartbeat | nothing | `bko-farm-heartbeat.service`; `/var/log/bko-farm/heartbeat.log`, `/var/log/bko-farm/status` |
| panic sysctls | `/etc/sysctl.d/91-bko-farm-panic.conf`, only where nothing else sets them | |

Bigfoot's settings: `/etc/bko-farm/hosts` (one `NAME ADDRESS [SEED-IP]`
per line, re-read every cycle), the journal-remote drop-ins
`/etc/systemd/journal-remote.conf.d/bko-farm.conf` and
`/etc/systemd/system/systemd-journal-remote.service.d/bko-farm.conf`,
`/etc/logrotate.d/bko-farm`. The two services run as the system user
`bko-farm`, at nice 10, with at most 128 MB each.

Disk on Bigfoot (55G root, 40G free on 2026-11-17): journal-remote keeps
at most 6G and leaves 10G free (`MaxUse=6G`, `KeepFree=10G`, files of
128M); the logs under `/var/log/bko-farm` rotate weekly or at 50M, 8 kept;
a netconsole file over 256 MiB takes no more lines until it is rotated.

## Not encrypted, not authenticated

Both the journal (HTTP on 19532) and netconsole (UDP 6666) are plain text
on the LAN, with no TLS and no client check: anyone who can reach those
ports can read nothing back but can write into Bigfoot's logs. Bigfoot's
ufw limits both to 192.168.68.0/24. Journal files are named by the
sender's IP, not by a certificate.

## Install

1. On Bigfoot, from a copy of this directory:

       ssh m@bigfoot.local 'rm -rf bko-farm-monitor'
       scp -r testvm/farm-monitor m@bigfoot.local:bko-farm-monitor
       ssh -t m@bigfoot.local 'sudo bko-farm-monitor/install-collector.sh'

   It installs `systemd-journal-remote`, the two services, logrotate, and
   prints what listens and the first heartbeat status.

2. Bigfoot's ufw. The installer never changes the firewall; it checks for
   these two rules and prints them when missing. The user runs:

       sudo ufw allow from 192.168.68.0/24 to any port 19532 proto tcp
       sudo ufw allow from 192.168.68.0/24 to any port 6666 proto udp

3. On each farm host, one at a time:

       ssh m@cage.local 'rm -rf bko-farm-monitor'
       scp -r testvm/farm-monitor m@cage.local:bko-farm-monitor
       ssh -t m@cage.local 'sudo bko-farm-monitor/install-sender.sh'

   The argument is the collector, default `bigfoot.local`. The journal URL
   uses that name when the host resolves it (`getent ahostsv4`; all four
   did through nss-mdns on 2026-11-17), so a move of Bigfoot's lease is
   followed. Otherwise it uses 192.168.68.197 (`--collector-ip` to change)
   and says so. `--no-panic` skips the panic sysctls.

All three scripts are idempotent: run them again after changing a file
here. `--help` shows each one's steps.

## The netconsole target

`bko-netconsole` runs at boot and every 10 minutes. It takes the
lowest-metric default route's interface (cinqcent: `enp3s0`, not its Wi-Fi
`wlp4s0`), the source address and next hop from `ip route get`, the next
hop's MAC from one ping and `ip neigh` (Bigfoot itself, on the same /24),
and writes them to the configfs target
`/sys/kernel/config/netconsole/bko-farm`. It rewrites the target only when
something changed, disabled while it does. When it can't find the MAC it
uses broadcast, netconsole's own default, and says so in
`journalctl -u bko-netconsole`. Wi-Fi drivers mostly can't do netconsole
(no netpoll); a host whose default route is Wi-Fi will fail to enable.

The target uses the extended format (`extended=1`): each message carries
its level, a sequence number and the kernel's own timestamp, and a long
message is split in numbered fragments instead of cut. A gap in the
sequence numbers shows lost packets. The 7.2 kernels have the attribute
(`CONFIG_NETCONSOLE_DYNAMIC=y`; `CONFIG_NETCONSOLE_EXTENDED_LOG` only sets
the default, which is off).

### Log level

The hosts boot with `quiet`, so the console log level is 4: only levels 0
to 3 (emergency to error) reach any console, netconsole included.
`bko-netconsole` raises it to 5 (`dmesg -n 5`), once per boot, never
lowers it, and keeps the first level it found for `--remove`. Why 5:

- the soft lockup, hard lockup and oops headers are emergency or alert,
  RCU stalls and hung tasks are error: those pass at 4 already;
- but the register dumps and stack traces after a soft lockup, and the
  NMI backtraces of an RCU stall, are printed at the default level (4,
  `KERN_DEFAULT`) or as warnings, and `WARN_ON` splats are warnings: those
  need 5;
- 5 stops notice and info (link changes, bridge ports of every test VM,
  USB, module loads), which would be most of the traffic.

A panic sets the level to 15 by itself, so everything printed during a
panic goes out anyway. A hung task's stack (`task:... state:D`) is info:
for that, re-run the sender with `--console-loglevel 7`, at the cost of
the info noise on the console and the wire. The level applies to every
console, so the local screen shows warnings too.

### Panic sysctls

The sender sets `kernel.panic=10`, `kernel.softlockup_panic=1` and
`kernel.hardlockup_panic=1`, each only when it is 0 and no sysctl file
sets it, in `/etc/sysctl.d/91-bko-farm-panic.conf`. A lockup then panics,
netconsole carries the panic out, and the host reboots 10 s later.

Cage already has all three, and more, from 2026-11-13: see Cage's
`/etc/bko-watchdog.README` (the SP5100 TCO watchdog through systemd's
`RuntimeWatchdogSec=60s`, lockups and oopses panic, panics reboot after
10 s and leave their dmesg in efi_pstore; files
`/etc/modules-load.d/bko-watchdog.conf`,
`/etc/systemd/system.conf.d/bko-watchdog.conf`,
`/etc/sysctl.d/90-bko-lockup.conf`). The sender sees
`90-bko-lockup.conf`, adds nothing there and leaves that setup alone.

## Reading the results

On Bigfoot. The journal files belong to `systemd-journal-remote`, so
`journalctl` needs sudo.

- Who is up now: `cat /var/log/bko-farm/status` (state, IP, since, last
  up, last answer of any kind).
- Every change: `cat /var/log/bko-farm/heartbeat.log`, or
  `journalctl -t bko-farm-heartbeat`. A freeze reads like:

      cage: up -> down at 2026-11-16T22:13:15+01:00 (192.168.68.199; was up for 6h13m; last up 2026-11-16T22:13:00+01:00)
      cage: down -> up at 2026-11-17T01:40:30+01:00 after 3h27m15s not up (since 2026-11-16T22:13:15+01:00; 192.168.68.199)

  A change is logged once two probes in a row agree, dated from the
  first. `ping-only` means the kernel still answers ping but sshd gives no
  banner: either no TCP at all, or a connection the kernel accepted with
  nothing behind it (the probe waits for the `SSH-` banner because a
  frozen userspace still completes the TCP handshake).
- A host's last log lines before a freeze:

      sudo journalctl -D /var/log/journal/remote _HOSTNAME=cage --until '2026-11-16 22:14' -n 100
      sudo journalctl -D /var/log/journal/remote _HOSTNAME=cage -k -n 50     # kernel only
      sudo journalctl --file '/var/log/journal/remote/remote-192.168.68.199*' -f

  One file per sending IP: after a DHCP move a host has two; `_HOSTNAME=`
  finds both.
- The kernel's last words: `tail -n 50 /var/log/bko-farm/netconsole/by-name/cage.log`
  (or `IP.log`). Each line is Bigfoot's time, the host name (`?` when the
  IP is not in `/etc/bko-farm/hosts`) and the extended record
  `prefix,seq,usec-since-boot,flags;text`. The prefix is facility*8+level:
  for the kernel's own messages (facility 0) it is the bare level; a line
  written to `/dev/kmsg` is facility user (1), so `<4>` shows as `12`.
- The services themselves: `journalctl -u bko-farm-heartbeat -u bko-farm-netconsole -u systemd-journal-remote`.

On a host: `sudo bko-netconsole --status`, `journalctl -u bko-netconsole`,
`systemctl status systemd-journal-upload`.

## Testing

From a host, after its install:

    logger -t bko-farm-test "hello from $(hostname)"
    echo "<4>bko-farm netconsole test" | sudo tee /dev/kmsg

On Bigfoot, within a few seconds:

    sudo journalctl -D /var/log/journal/remote -t bko-farm-test -n 5
    grep 'bko-farm netconsole test' /var/log/bko-farm/netconsole/by-name/cage.log

The `<4>` message is a warning: it reaches netconsole only because the
level is now 5, so it tests that too. The heartbeat: `sudo systemctl stop
ssh` on a host for a minute gives `up -> ping-only` and back (keep the
current session open, it survives).

## Costs

- Each probe is one TCP connection to the host's sshd, which logs it
  (about one `Connection closed by 192.168.68.197` line every 15 s per
  host); those lines come back to Bigfoot in the journal stream.
- Bigfoot runs two small Python processes and journal-remote, and takes
  the four journals' write rate on its 990 PRO.
- A host's journal goes out once more over the LAN.

## Uninstall

On each host, then Bigfoot:

    sudo bko-farm-monitor/install-sender.sh --uninstall [--purge]
    sudo bko-farm-monitor/install-collector.sh --uninstall [--purge]

The sender removes the target, puts the log level and its panic sysctls
back, and disables journal-upload. The collector removes its services,
drop-ins, logrotate, user and files, and keeps what it received unless
`--purge`. `--purge` also removes the package `systemd-journal-remote`,
only where this installer installed it. Then the ufw rules, if added:

    sudo ufw delete allow from 192.168.68.0/24 to any port 19532 proto tcp
    sudo ufw delete allow from 192.168.68.0/24 to any port 6666 proto udp
