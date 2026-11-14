# One-build confirmation of HoltTest (2026-11-14)

The note being answered: "The Linux passes come from mixed builds … The confirming
full run (r32) is still owed … best done once the *Test2 RAM changes land, at about
half the disk writes."

## What is true today

- **The note is from 2026-11-06 and is out of date.**
  - **r32** was the old HoltTest1All run of 2026-11-06. It was stopped when Chopin's
    /bkoscratch filled (docs/design/test2/data/ht1-runs.tsv, rows `r32`).
  - **The RAM changes** are test2.md's W1, "~200 → ~103 GB a run, or ~0–5 GB; no full
    disks (r32)". W1 (and W2) **landed on 2026-11-08** (e8a8d91, 6fff54b).
  - **The confirming run** happened that same day as `r4all`: 48 of 49 passed on one
    build, bko-holt 0.2.4 bef913e (testvm/holttest1d/README.md, "Run `r4all`").
    `freebsd15-geli` is the one that didn't pass: it has no GELI support.
- **What is mixed now.** The last PASS of each holt cell, from the merged Test2 ledger
  (Mac mirror, Chopin's lost rows included):
  - **58 of the 72 cells** last passed on one binary: the shipped `holt-bin`, Linux
    bko-holt sha256 `24b1cfef0663`. That is 0.2.4, built at 5b2fe9d. Most of those
    passes come from HoltTest2 run `t2h-20261112T0838-8e28`, on both hosts.
  - **These Linux guests are on other builds:**

    | Guest | Build it last passed on |
    |---|---|
    | `gentoo`, `gentoo-systemd` | `7760c9e7c497`, the gentoo-wt build (G5) |
    | `nixos2605` | set `629bc7b8`, 2026-11-12 00:11 |
    | `cachyos-zfs` | no PASS yet (new 2026-11-13) |
  - Also on other builds: the old `+fitbigger`/`+smaller`/`+refuse`/`+img` variant cells
    and alpine324-luks/-lvm/-uefi, all from 11-10/11.
- **Nobody has tested the current code.** holt/src has 16 commits beyond 5b2fe9d:
  Gentoo G2/G3, NixOS subvolumes and UEFI, the `load --erase` series, and the CachyOS
  exact-rt fix.
  - **0.2.5 (8d190c9) was never promoted to `holt-bin`.** All four hosts still have
    0.2.4 at 5b2fe9d. HEAD is 8 holt commits past 0.2.5.
  - So `t2 plan HoltTest2` marks **all 65 cells due** (`holt:code`).

## Options (estimates from `t2 plan HoltTest2 --hosts chopin,cinqcent,cage,copland`)

| | What | Wall clock | Guest-min | Disk writes | Gives one build? |
|---|---|---|---|---|---|
| (a) | HoltTest2 now, bko-holt built at HEAD, 4 hosts | ~39 min + the bko-holt build | ~296 | ~0.5 GB (RAM-first) | yes, all 65 cells |
| (b) | Land the RAM changes first, then (a) | same as (a) | same | same | yes |
| (c) | Rerun only the guests off `24b1cfef` | ~15 min | ~15 | ~0.1 GB | no (see below) |

- **(b) is already done.** W1 landed on 2026-11-08; the 0.5 GB estimate is its result.
  The note's "about half the disk writes" was W1's first target, ~103 GB of ~200 GB.
- **(c) doesn't confirm anything.** Rerunning `gentoo`, `gentoo-systemd` and `nixos2605`
  on `24b1cfef` fails on Gentoo, because the build has no G2. Rerunning them on HEAD
  leaves the other 55 on 0.2.4, which is still mixed. It also leaves the 16 commits
  since 5b2fe9d untested on the other guests.

## Recommendation: (a) under HoltTest2, not ht1-queue.sh

```
testvm/test2/t2 HoltTest2 --hosts chopin,cinqcent,cage,copland
```

- **What it runs.** The bare word runs the 65 due cells: every source, plus the
  bigger/oneshot/degraded variants whose golden plans changed. It runs on one bko-holt
  that it builds from HEAD on chopin+cinqcent and ships to all four hosts.
- **Slots and rustic-bko.** Host slots come from hosts.json: big hosts 3 jobs, small
  hosts 1. rustic-bko is the shipped `bko-bin`.
- **Linux only.** Add the 41 Linux names to the same command to skip the BSD, illumos
  and Haiku cells. That saves ~15 min, but it leaves those platforms on 0.2.4.
- **What a pass gives.** Every cell then has a PASS on one bko-holt build, and that build
  becomes the candidate for `holt-bin`.

**Before launching:**

- **Commit the other sessions' changes first.** The tree has uncommitted edits from
  other sessions, `testvm/holttest1d/build-holt.sh` among them. Under the committed rule
  (copies.py), a row from a dirty copy counts only if HEAD later matches it, so those
  edits need to be committed first.
- **Let the fork sessions finish.** "DragonFly rustic-bko rebuild + push forks" may
  replace `bko-bin`, so wait for it.
- **Leave Cinqcent's relink alone.** Check that `/bkoscratch/relink-20261114` is idle
  before Cinqcent takes cells.
- **Put guests on Cage and Copland.** They hold few guests. Cells marked "any" need
  `vm-store link` there, or the planner keeps them off those hosts.
- **Add a watchdog.** Use `t2 resume RUN` after a stall.
