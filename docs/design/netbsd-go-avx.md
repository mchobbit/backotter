# NetBSD: restic-bko built by a fixless Go stores wrong bytes

Status: cause found and guarded, 2026-11-24.

## What was seen

SticTest2 run `t2s-20261115T1944-05fb` (rel9, cinqcent, guest alpinebsd): the
restic->rustic direction of `testvm/compare/netbsd/roundtrip.sh` restored the
1 GiB sparse file `sparse` with a different SHA-256 and 208 KB allocated where
the source has 96 KB. The other three directions passed. A restore over the
existing tree kept the wrong bytes, since they were what the snapshot holds.
The failure was not seen again in about 98 runs, so it looked like a flake.

## Cause

[golang/go#80285](https://github.com/golang/go/issues/80285): NetBSD amd64, at
least under QEMU/KVM on AMD Zen 2/3 and Ryzen 7 255 hosts, does not keep the
upper halves of the YMM registers across signal delivery. Go's async
preemption signals interrupt its AVX `memmove`, so the copy of a chunk buffer
is corrupted *before* restic hashes it. The bad blob is stored under the hash
of its bad bytes, so `restic check --read-data` passes and every tool restores
the same wrong bytes. rustic-bko's restore was faithful.

Go's fix turns AVX off on netbsd/amd64 (`src/internal/cpu/cpu_x86_netbsd.go`).
It is in go1.26.8 and later on the 1.26 line (release-branch.go1.26 d1434db)
and in go1.27.0 and later. No go1.25.x will have it.

## Evidence

Reproduced on the farm by a loop that backs up with restic-bko and restores
with rustic-bko (`~/backotter-work/sparse-repro/sr5.sh` with `tight3.sh`,
`CPUS=4 FRESH=1`, on every host):

| restic-bko | Go | bad backups |
|---|---|---|
| rel9 | go1.25.10 | 4 / 1304 |
| rel15, rel16 | go1.26.10 | 0 / 2796 |

In each hit, `restic dump`, `rustic dump` and restores by both tools gave the
same bad bytes. A plain reader of the source file ran 5730 times during the
hits and always saw the expected bytes, so the kernel's reads were right and
the damage was inside the restic-bko process. All four hits were on 4-vCPU
guests on chopin, cinqcent and cage. 41 code-reading agents found no mechanism
in rustic-bko's restore or restic-bko's backup.

## Which binaries

- restic-bko rel6 to rel11: go1.25.10, affected on NetBSD.
- rel13 onward, including the promoted `bko-bin` sets: go1.26.10, fixed.

Backups made on NetBSD by restic-bko rel11 or older can hold silently wrong
data. Only test repositories are known to have been made with them.

`GODEBUG=asyncpreemptoff=1` is not a fix: other signals can trigger it too.

## Guards

- `testvm/go-netbsd-fix.sh`: `go_netbsd_avx_fixed` takes `go version` text and
  says whether that Go has the fix (prereleases and devel builds count as
  fixless). Tests: `testvm/go-netbsd-fix-test.sh`.
- `stick/build-round.sh`: refuses to build the netbsd restic-bko with a fixless
  Go and checks the Go that the built binary embeds. `BKO_ALLOW_FIXLESS_GO=1`
  overrides with a warning.
- `testvm/compare/rustic-exact-round.sh`: its go1.25.10 pin is gone. go1.25.10
  matched restic's go.mod toolchain line and stock 0.19.1's build. Build hosts
  now need a fixed Go; `ALLOW_OTHER_GO=1` warns and passes the override on.
- `testvm/promote-bins.sh`: a `bko-bin` set whose netbsd restic-bko embeds a
  fixless Go is promoted nowhere.
- `roundtrip.sh` keeps its fx capture (447df83) for any later wrong bytes.
