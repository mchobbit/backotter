# Farm telemetry to Bigfoot

Every 15 minutes each farm host (chopin, cinqcent, cage, copland, choubox)
sends one batch to Bigfoot: its journal since the batch before (kernel messages
included) and a snapshot of its state. After a host freezes, the batches
show what it was doing before, and the time the batches stopped dates the
freeze. Written after Cage froze twice on 2026-11-16 (16:00 idle, 22:13
under light load): ping answered, sshd closed new connections, and its own
journal just stopped.

It runs beside `testvm/farm-monitor/` (live journal upload, netconsole, a
15 s heartbeat): farm-monitor has the moment of a freeze, these batches the
host's state around it (memory, pressure, SMART, D-state stacks, reservations).

## Where things are

| what | where |
|---|---|
| the push script | each host: `/usr/local/sbin/bko-telemetry` (copied from here) |
| its timer | `bko-telemetry.timer`: `*:0/15`, up to 60 s random delay, `Persistent=true` (a run missed while the host was down happens at boot) |
| its settings | `/etc/default/bko-telemetry` (Bigfoot's name and IP, the caps) |
| its state | `/var/lib/bko-telemetry/`: `id_ed25519` (its own key), `known_hosts` (Bigfoot, pinned), `journal.cursor` and `journal.since` (where the next batch's journal starts), `spool/` (batches not yet sent), `spool/refused/` |
| its RAM side | `/run/bko-telemetry/`: the lock, the work dir, the new batch (`out/`), copies of the key and the journal position |
| its log | `journalctl -u bko-telemetry.service -o cat`, one line per run |
| the receiver | Bigfoot: `/usr/local/bin/bko-telemetry-recv`, run as user `bkotel` |
| the store | Bigfoot: `/var/log/bko-telemetry/HOST/YYYYMMDDTHHMMSSZ.tar.gz` |

A batch is named by its UTC start time on the host and holds three files:

- `journal.txt`: `journalctl -o short-iso-precise` from the cursor on (the
  first batch: the last 15 minutes). At most 40 MB: when the read is bigger
  or slower than 60 s, the batch keeps the first 20 MB of it, then the
  newest 100000 entries (a second read back from now, at most 20 MB), and
  says what it dropped; the next batch starts after those newest entries,
  so a log storm can't leave every later batch stuck on old entries.
- `snap.txt`, in this order: first what lives in RAM (uptime, load,
  `free`, meminfo, PSI, zram, ZFS ARC, the last 300 dmesg lines, D-state
  tasks with their kernel stacks, `ps` by CPU and by RSS, process and
  thread counts, qemu processes, `top`, thermal zones, `systemctl
  --failed`, the test scopes, `ip -s link`, `ss -s`, the tail of
  cmp-run.sh's ledger (`/mnt/bko-ram/.reserve`, `.reserve.wait`), pstore (a
  kernel oops saved across reboot)); then what touches disks: `nvme
  error-log` and `smart-log`, `df`, `/bkoscratch*` mounts, `zpool status
  -x`, `smartctl -a` per disk, sensors, `journalctl --list-boots`,
  ssh.service.
- `meta.txt`: host, batch, boot id, uptime, the script's hash, and a `note`
  for anything cut short.

A host that freezes may have a stalled root disk, so the run works in RAM
(`/run/bko-telemetry`: lock, work dir, the new batch) and touches
`/var/lib/bko-telemetry` only in bounded steps. Each probe runs under
`timeout 20` in its own bounded step (27 s), and once a device has a probe
stuck, its other probes are skipped; a probe stuck in D state (which no
signal ends) is left behind. The journal read (60 s) and the snapshot
(120 s) are bounded as a whole too. A missing tool is skipped; every cut
gets a note.

The new batch is pushed first, straight from RAM. Only if that fails does
it go to the disk spool (if the disk doesn't take it in 20 s, it waits in
`/run/bko-telemetry/out`, at most 20, for the next run). Then the spool
goes, oldest first; a batch leaves it only once Bigfoot has stored it.
While Bigfoot is away, batches wait (at most 1 GiB or 2000 files, oldest
dropped first). The journal position moves on in RAM once the batch is
built, and on disk once the batch is pushed or spooled, so after a reboot
the first batch rereads anything not safely sent. A saved cursor that
journalctl rejects (say, damaged by a hard reset) is set aside and the read
starts from the last batch's start time instead.

## How it is locked down

Each host has its own ed25519 key. Bigfoot's `~bkotel/.ssh/authorized_keys`
has one line per host:

    restrict,command="/usr/local/bin/bko-telemetry-recv cage" ssh-ed25519 AAAA... bko-telemetry@cage

The host name comes from that line, not from the client, so one host can't
write into another's folder. The receiver takes only a file name of the
form `YYYYMMDDTHHMMSSZ.tar.gz` that is a real UTC time at most a day ahead
of Bigfoot's clock (or `ping`); at most 64 MiB, unpacking to at most
256 MiB, a valid gzip'd tar of at most three regular files named
`journal.txt`, `snap.txt` and `meta.txt`. One upload per host at a time,
and a read that takes over 5 minutes is dropped. It can't list, read back
or run anything. `bkotel` has no password and no sudo, and can't change its
own keys: `~bkotel/.ssh` and `authorized_keys` belong to root.

Each host pins Bigfoot's host key (`StrictHostKeyChecking=yes`). Setup
reads that key from Bigfoot's `/etc/ssh` over the Mac's own ssh session to
it (which the Mac's known_hosts checks), never from an unchecked scan, and
each host's own scan of Bigfoot must match it.

Room: each host may keep 8 GiB / max(4, number of hosts), so 1.6 GiB with
five; over that, its own oldest batches (by arrival) go, so one host's key
can never push out another host's history. At most once an hour, batches
older than 30 days go too. The receiver refuses new batches (the hosts
keep them) when storing one would leave Bigfoot's `/var` under 2 GiB free.

Daily, `bko-log-prune.timer` on Bigfoot (installed by setup) prunes every farm
log store there to 30 days, even when no batch arrives: the telemetry batches
(`bko-telemetry-recv --prune`, as bkotel), farm-monitor's
`/var/log/journal/remote` (`journalctl --vacuum-time`) and the rotated copies
under `/var/log/bko-farm` (never the live `.log` files). One line per store in
`journalctl -u bko-log-prune`.

## Install

From the Mac, at the repo root:

    testvm/telemetry/telemetry-setup.sh --dry-run    # what it would do
    testvm/telemetry/telemetry-setup.sh              # all five hosts
    testvm/telemetry/telemetry-setup.sh cage         # one host

It is idempotent, so run it again after changing a file here: it
reinstalls the receiver, the script and the units, keeps each host's key
and `/etc/default/bko-telemetry`, and replaces each host's key line on
Bigfoot. It ends each host with one run and shows its log line and the
newest batch on Bigfoot.

The script is copied to `/usr/local/sbin` rather than run from
`~/backotter-work`, so a sync-hosts.sh (or a checkout half-way through a
change) never swaps the file under a running copy. The cost: a change here
reaches the hosts only when setup runs again.

## Reading it after a freeze

On the Mac:

    testvm/telemetry/telemetry-read                  # each host: newest batch, how long silent
    testvm/telemetry/telemetry-read cage             # the last day of batches, gaps flagged
    testvm/telemetry/telemetry-read cage --at "2026-11-16 22:13"
    testvm/telemetry/telemetry-read cage --grep 'nvme|I/O error|hung_task|Out of memory'

A gap over 20 minutes between batches means the host made none: down,
frozen, or the timer stopped. A batch that "arrived late" was made on time
but spooled (Bigfoot or the network was away).

`--at` takes the Mac's local time (`--utc` for UTC), fetches the batches
from 30 minutes before to 30 after, plus the first batch after that time
however late (after a freeze, that is the one sent after the reboot; it
holds whatever of the journal reached the disk before the freeze), into
`~/backotter-work/telemetry/HOST/at-STAMP/`: one folder per batch and a
combined `journal.txt`. It prints the journal lines just before the time.

What to look at first: the last batch's `snap.txt` (memory, PSI, D-state
tasks and their stacks, the disks' error logs, the dmesg tail), then the
journal of the first batch after the gap, then `journalctl --list-boots` in
the next snapshot.

`telemetry-read` reads as `m@bigfoot.local`. `m` is in group `bkotel`,
which owns the store (`2750`, files `0640`); a dedicated group rather than
`adm`, so being able to read these batches doesn't come with every other
log on Bigfoot. Until m logs in again after setup, the group isn't in
effect and the tool falls back to `sudo -n`.

## Limits

- What never reached the journal is lost: the batch holds what journald had
  written by the time of the run. A freeze can still hide the last up to 15
  minutes. Catching the very last kernel messages would take netconsole to
  Bigfoot; that is not set up.
- `IOSchedulingClass=idle` only counts under the BFQ scheduler; on NVMe
  (`none`) it does nothing. Under BFQ and constant heavy I/O it could slow a
  run until the 60/120 s bounds cut it short; the notes say when.

## Privacy

The batches hold each host's full journal: commands run by root, unit
output, user names, paths, guests' names. They stay on Bigfoot's own disk
(`/var`, its SSD), not on DantePool, so they are not copied to the gmk
backups. Readable by root and group `bkotel` (m) only.

## Removing

    testvm/telemetry/telemetry-setup.sh --uninstall cage          # one host
    testvm/telemetry/telemetry-setup.sh --uninstall chopin cinqcent cage copland choubox

This stops and removes the timer, units, script, settings and
`/var/lib/bko-telemetry` (key and spool) and `/run/bko-telemetry` on each host, and drops the host's
line on Bigfoot. The stored batches stay; to remove those and the receiver
too, on Bigfoot:

    sudo rm -rf /var/log/bko-telemetry /usr/local/bin/bko-telemetry-recv
    sudo gpasswd -d m bkotel; sudo userdel -r bkotel

## Files here

| file | what |
|---|---|
| `bko-telemetry` | the push script (each host) |
| `bko-telemetry.service`, `bko-telemetry.timer` | its units |
| `bko-telemetry.default` | the first `/etc/default/bko-telemetry` |
| `bko-telemetry-recv` | the receiver (Bigfoot) |
| `bko-log-prune` | Bigfoot's daily prune of all farm logs (`bko-log-prune.timer`) |
| `telemetry-setup.sh` | install and remove, from the Mac |
| `telemetry-read` | read the store, from the Mac |

Local tests: `bko-telemetry --no-push` builds a batch into
`/run/bko-telemetry/local/` and leaves the spool and the journal position
alone. `BKO_TEL_CONF` (a settings file: `BKO_TEL_SSH` for a fake ssh, the
caps and bounds), `BKO_TEL_STATE` and `BKO_TEL_RUN` move the push script's
files; the receiver takes `BKO_TEL_STORE`, its caps and `BKO_TEL_TEST_PATH`
(stand-ins for `flock` and `timeout` on a Mac), and ignores all of them
whenever it runs under sshd (`SSH_CONNECTION` set), so a client can't steer
it with them.
