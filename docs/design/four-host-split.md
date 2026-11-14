# Four-host split: why Cage and Copland sat idle, and the fix

Status: root cause written 2026-11-14 (evidence below); fix in the same
change set. Hosts: chopin, cinqcent (big), cage, copland (small), all amd64.
Bigfoot (arm64) and czerny (reserved row) are out of scope.

## What was seen

2026-11-14 about 15:30 UTC: Chopin at load 14.6 on 12 cores with about 12
`cmp-run.sh` jobs, several waiting for RAM; Cinqcent at 7.1 with about 16;
Cage and Copland at load 0 with empty RAM disks. Chopin's line jammed from
14:00 to 15:10, and a HoltTest2All waited about 2 h for t2's holt family
lock behind a one-cell `HoltTest2 freebsd15-geli` that was itself waiting
for Chopin RAM (run t2h-20261114T1320-0f53, `hosts chopin cinqcent`).

Measured from each host's `/bkoscratch/wear/jobs.log` (finished
`cmp-run.sh` jobs only, job-hours = sum of their run seconds):

| host | jobs 11-14 | job-hours 00-09Z | job-hours 09-16Z | what ran 09-16Z |
|---|---|---|---|---|
| chopin | 363 | 27.8 | 14.2 | t2 SticTest2, ht1.sh, make-ubuntu-core, make-truenas, make-void-zfs, vm-update |
| cinqcent | 775 | 15.8 | 19.3 | bs2-host (368), ht1.sh, t2 SticTest2 |
| cage | 488 | 3.9 | 3.5 | bs2-host, the Qubes Q0 work |
| copland | 419 | 5.3 | 3.5 | bs2-host |

Almost everything that reached the small hosts came from the one tool whose
default already spans all four hosts (BackShot2's `bs2`, since c86fd06) and
from the Qubes spec, which names Copland explicitly. Of the 22 t2 runs
started on 11-14, every one ran on `chopin cinqcent` or a subset of them;
none on a small host.

## Root cause

There is no single cause. Five things stacked, and the first two decided it.

### 1. The small hosts never joined the defaults (the decisive one)

`testvm/hosts.json` had cage and copland at `"default": false`. t2 and
drive.py use the default hosts when `--hosts` is not given, so every
SticTest2, HoltTest2, GateTest2 and GuestTest2 ran on Chopin and Cinqcent.
The join rule (`docs/design/test2/design-classes.md` §2.6, testvm/HOSTS.md
"Adding a host" step 10) flips `default` only after the HostTest2 layers
and one representative per family pass on the host.

Provisioning status of the 2026-11-13 plan (four-host-farm, f0def4f,
workflow wf_114b88b5-9e1), checked on the hosts 2026-11-14 16:40Z:

| step | cage | copland |
|---|---|---|
| sync (`.backotter-commit`) | 62fd97e (11-13), behind chopin's 214e4ea | same |
| guests linked from vm-store | 43 of 63 published | 60 of 63 |
| holttest1d bin (bko-holt, f2fs/jfs debs, bcachefs) | yes | yes |
| rescuer.qcow2 + rescuer-uefi.qcow2 (+ .sha256) | yes (copied 11-13) | yes |
| bko-bin rel8, `sha256sum -c SHA256SUMS` | OK | OK |
| /bkoscratch/compare-disks | yes | yes |
| ssh: Mac, chopin, cinqcent, the other small host | yes | yes |
| ssh to itself | no (key not in its own authorized_keys) | no |
| facts probe (`t2 plan ... --hosts`) | works | works |
| `cmp-run.sh --ceiling` | 9.0 11.2 | 9.0 11.2 |
| trial runs (gate + one per family) | never | 11-13 03:00Z, all HARNESS, then set aside |

The trial runs are where it stopped. On 11-13 at 03:00Z GateTest2 and
HoltTest2 were tried on Copland (runs t2g-20261113T0309-4ba8,
t2h-20261113T0259-282d). Both loop gates failed as HARNESS ("--reserve 14 4
can never fit here (at most 9.0 9.2)": the small class's `host_reserve` did
not exist yet), and the holt cells failed as HARNESS (the rescuer and debs
were copied to Copland only later that day). Then the user asked to stop the
Test2 tests, and the runs were set aside. The fixes landed later on 11-13
(small `host_reserve` in gate/layers.json, the rescuer and bin copies, the
SN770 moves of both M900s), but no session owned "rerun the trials and flip
the defaults": it was a closing step of a finished workflow, written down
only as a condition in a memory file ("flip default ... only after ...").

### 2. Sessions place their own work, and everything they read says "both hosts"

Most load is not t2. It is the OS-support sessions running `ht1.sh`,
`make-*.sh`, `slk-boot.sh` and the like under `cmp-run.sh --reserve` on a
host they choose. They choose Chopin and Cinqcent because that is what they
are told:

- Auto-memory: `split-chopin-cinqcent.md` ("split ... between Chopin and
  Cinqcent") is the only rule about where work goes, and 26 memory files use
  "both hosts", "chopin+cinqcent" or "chopin and cinqcent" 90 times, mostly
  as "HoltTest1 PASS both hosts", which reads as the definition of done.
- Specs: 28 of the 40 design specs in docs/design/ write their test steps as
  "both archivers, both hosts" or "copy to Cinqcent" (slackware.md S5/S6,
  solus.md S4, ...). A spec approved with that wording is a plan to use two
  hosts.
- READMEs: testvm/holttest1d/README.md names chopin 23 times and cinqcent 24,
  cage and copland twice each; testvm/store/README.md 17/1/2/2.
- Syncs: the hosts' `.backotter-commit` shows chopin and cinqcent at 214e4ea
  (synced 11-14 14:48) and cage and copland at 62fd97e (11-13): sessions
  have been syncing with an explicit `m@chopin.local m@cinqcent.local`
  (since b80e98d the bare default includes Bigfoot, which must not be
  touched now), so the small hosts also run older scripts.

### 3. Guests missing on the small hosts

A small host's `vms/` is links into its vm-store cache. Cage lacked 20 of the
63 published guests and Copland 3, because `vm-store link --all` after a
publish is manual. And 26 guests on Chopin are not published at all: every
guest an OS-support session built in the last two days (biglinux,
elementary8, garuda, guix15, kali, neon24, oi2610, pikaos4, proxmox9,
slackware15, truenas25, uc24, void-zbm, ...). New-guest work can only run
where the guest is, which is Chopin, plus Cinqcent when the session copies
it there by hand ("copy to Cinqcent with the same SHA-256" is a spec step).

Linked guests can also be at the wrong generation. A `vm-store link` pins
one version, and a later publish doesn't move the link until someone runs
`vm-store link GUEST` (or `--all`) on that host. On 2026-11-14 16:55Z, two
of the 70 guests' image-state generations differed between hosts:
void-musl (Cage linked to g2aaa1c9e3ba from 11-12; latest if368b8a0a46 from
11-13) and alpine324 (Cinqcent's full folder at gd562e3effb42; the other
three at the latest, g3f10bf889f68). This hurts more than a missing guest.
t2's identity step (`guest.py` `_identity_compare`) drops a cache host that
lacks a guest, but when the hosts hold different generations it fails the
guest on every host. The plan then gives each of the guest's stic and holt
cells "its guest's identity failed" and runs them nowhere. So one stale
link on a default host takes a guest out of the whole farm, and the
cautious move, keeping the host out of the defaults, looks like the safe
one. BackShot2 has its own form of this: a case prepared at an older
generation is refused as "not prepared at its current generation"
(`bs2-host`). On 11-14 that left BackShot2All's 30 deferred tier 2 cases on
Cinqcent only once Copland's Alpine guests moved to the store version.

### 4. Small-host limits (real, but not the reason they were idle)

The small hosts' ceiling is 9.0 GiB of RAM disk and 11.2 GiB of memory
(BASE_GIB 4), with `holt_jobs 1` and `stic_slots 2`. That rules out the
big jobs: stic debian13-zfs (`--reserve 13 5`), ubuntu26-zfsmirror (23
GiB), vanilla3 (30 GiB), every `holt.needs_host_zfs` source, image builds.
It does not rule out most of what queued on Chopin today: most HoltTest
lines are under 9 GiB in `ram-sizes.tsv`, and t2's placement and
ht1-queue.sh already keep over-ceiling lines off a small host. The limits
explain why a small host takes less, not why it took nothing.

### 5. No shared scheduler

There was no way to ask "where is there room now?". `cmp-run.sh` queues
inside one host only; each session picks a host by name; t2 places only its
own cells; ht1-queue.sh shares one todo between hosts but only the hosts a
session starts runners on. So two sessions that each "split between Chopin
and Cinqcent" both pile onto the same two hosts. The holt family lock adds
to it: one HoltTest2 at a time Mac-wide, so a one-cell run stuck in one
host's RAM line blocks every other HoltTest2 for as long.

## The fix

1. **Provision and join.** Link every published guest on both small hosts;
   self-ssh keys; sync all four hosts; rerun the trials (GateTest2 and one
   representative per family `--hosts cage,copland`); set `default: true`
   for both. t2, drive.py and SweepTest2 then use four hosts without
   `--hosts`, and placement still keeps over-ceiling, host-ZFS, pinned and
   unpublished-guest cells on the big hosts (testvm/HOSTS.md, "What stays on
   the big hosts").
2. **A placement helper.** `testvm/pick-host` probes the default hosts in
   parallel (`cmp-run.sh --free` and `--ceiling`, load per core, the guest's
   image, zpool, rescuer, debs), drops hosts that cannot ever take the job,
   and prints the best host for a `--reserve D M` now. Sessions call it
   instead of naming a host. `cmp-run.sh --free` is the new read-only
   query of the room a reserve could get right now.
3. **One command for a multi-host HoltTest1 queue.** `ht1-farm.sh` writes
   the todo and starts `ht1-queue.sh` runners on every default host, each
   with its own `holt_jobs`.
4. **Words that steer sessions.** The memory rule is now "spread over the
   four hosts with pick-host" (`split-chopin-cinqcent.md` rewritten), "PASS
   both hosts" is no longer the done criterion, and the READMEs' commands
   use pick-host or name all four hosts. Specs already approved are not
   rewritten; their "both hosts" now means "the hosts pick-host gives".

## Left open

- Publishing the 26 unpublished guests is the owning sessions' step (they
  are still changing them); until then their work stays on the big hosts.
- `vm-store link --all` after a publish is still manual on cache hosts, so a
  stale generation can come back after every publish. Two fixes are worth
  making: run link --all on each cache host from `vm-store publish` (Chopin
  can reach them all), and have the identity step drop a cache host whose
  generation is behind the fleet host's, as it drops one that lacks the
  guest, with the plan saying "kept off HOST: stale generation", instead of
  failing the guest everywhere.
- The holt family lock is Mac-wide by design (R21); a one-cell run can still
  hold it for as long as it waits for room. Not changed here.
- P19 (a fork-test lane on Cage) is still open.
