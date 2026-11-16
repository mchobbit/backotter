#!/usr/bin/env bash
# SticTest1: ubuntu26-arm64's targeted fixture test (Ubuntu 26.04.1 Server
# arm64 on Bigfoot, docs/design/arm64.md A5/A6: Subiquity's direct layout,
# an ESP at /boot/efi and ext4 /, under Secure Boot).  The guest's layout and
# OS are ubuntu26-uefi's, so its in-guest part is that check's: inner.sh,
# judge.py and nracl.py from ../ubuntu26-uefi (an ext4 like the guest's
# root, an ext4 /boot-like disk, its vfat ESP (a copy of /boot/efi) and an
# ext4 with project quotas and casefolding), run here on aarch64.
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR: holds restic-bko and rustic-bko for Linux arm64 (roundN/bin/linux-arm64)
# Boots a throwaway overlay (vm-run --snapshot, TMPDIR in /mnt/bko-ram/PREFIX)
# through guest-slot.sh and cmp-run.sh --reserve, runs inner.sh there (fixture
# on fresh loop ext4s, four directions, restore over immutable files, a
# non-root run, snapshot node identity, each tool checking the other's
# repository), judges with judge.py, prints PASS/FAIL/LIMIT lines, writes
# results.json; exit 0 if nothing FAILs.  Logs: $STC_LOGS/run-TIME/
# (STC_LOGS: ~/backotter-work/stictest1/ubuntu26-arm64/logs by default).
# Runs only on an aarch64 host (Bigfoot): the guest and the binaries are arm64.
set -uo pipefail
G=ubuntu26-arm64
HERE=$(cd "$(dirname "$0")" && pwd)
H=$(cd "$HERE/../ubuntu26-uefi" && pwd)   # the in-guest part: ubuntu26-uefi's
WORK=$HOME/backotter-work
C=${BKO_TESTVM:-$WORK/testvm}/compare
if [[ ${1:-} == --inner ]]; then
    shift; BIN=$1 PORT=$2 PFX=$3 OUT=$4
    . "${BKO_TESTVM:-$WORK/testvm}/vm-lib.sh"
    R=/mnt/bko-ram/$PFX/$G.$$
    mkdir -p "$R"
    trap 'vm_halt; rm -rf "$R"; rmdir /mnt/bko-ram/$PFX 2>/dev/null' EXIT; trap 'exit 143' TERM INT
    TMPDIR=$R VM_BOOT_TIMEOUT=300 vm_boot $G "$PORT" "$OUT/vm" --snapshot --memory 3072 || { echo "FAIL boot: $G didn't come up"; exit 1; }
    files=("$BIN/restic-bko" "$BIN/rustic-bko" "$C/torture.sh" "$C/fsmeta.py" "$C/rt-common.sh" "$H/inner.sh" "$H/judge.py" "$H/nracl.py")
    for f in "${files[@]}"; do
        vm_put "$f" "/root/$(basename "$f")" || { echo "FAIL copy: $f"; exit 1; }
    done
    T=60 vm_ssh 'uname -m' >"$OUT/guest-machine.txt" 2>&1
    # The in-guest part's tools, from the distribution when the image lacks one (the throwaway
    # overlay's apt: debian13-arm64's DVD install has no network source, so Debian's mirror is
    # added there, never in the guest's own image).
    T=600 vm_sh 'need=; for c in python3:python3 mkfs.vfat:dosfstools dumpe2fs:e2fsprogs; do command -v ${c%%:*} >/dev/null || need="$need ${c#*:}"; done
        if [ -n "$need" ]; then export DEBIAN_FRONTEND=noninteractive; { apt-get install -y -q $need || { apt-get update -q && apt-get install -y -q $need; } ||
            { . /etc/os-release; [ "$ID" = debian ] && echo "deb http://deb.debian.org/debian $VERSION_CODENAME main" >/etc/apt/sources.list.d/bko-st1.list &&
              apt-get update -q && apt-get install -y -q $need; }; } >/root/st1-apt.log 2>&1; fi
        echo "tools wanted:${need:- none}"; for c in python3 mkfs.vfat dumpe2fs; do command -v $c >/dev/null || { echo "MISSING $c"; tail -n 15 /root/st1-apt.log; cat /etc/apt/sources.list /etc/apt/sources.list.d/* 2>/dev/null | grep -v "^#" | head; }; done' >"$OUT/guest-tools.txt" 2>&1
    T=780 vm_sh "bash /root/inner.sh" >"$OUT/guest.out" 2>"$OUT/guest.err"
    echo "inner exit $?" >>"$OUT/guest.err"
    T=60 vm_ssh "cat /root/st1.tgz" >"$OUT/st1.tgz" 2>/dev/null && tar -C "$OUT" -xzf "$OUT/st1.tgz"
    exit 0
fi
[[ $# -ge 3 ]] || { sed -n 2,18p "$0" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PFX=$3 FILTER=${4:-}
[[ $(uname -m) == aarch64 ]] || { echo "FAIL harness: $G is an arm64 guest, and this host is $(uname -m)"; exit 1; }
for b in restic-bko rustic-bko; do [[ -x $BIN/$b ]] || { echo "FAIL binaries: no $BIN/$b"; exit 1; }; done
STC_LOGS=${STC_LOGS:-$WORK/stictest1/$G/logs}
OUT=$STC_LOGS/run-$(date +%Y%m%d-%H%M%S)
mkdir -p "$OUT"
. "$C/guest-slot.sh"
slot_take 3 "$(slot_range_here)"
HOT_C=90 "$WORK/cmp-run.sh" --reserve 3 5 timeout 1080 "$HERE/$(basename "$0")" --inner "$BIN" "$PORT" "$PFX" "$OUT" >"$OUT/run.log" 2>&1
slot_give
lines=$(grep -E '^(PASS|FAIL|LIMIT) ' "$OUT/guest.out" 2>/dev/null)
[[ -n $lines ]] || lines="FAIL check ran: no results (see $OUT/run.log, guest.err)"
grep -qx aarch64 "$OUT/guest-machine.txt" 2>/dev/null || lines+=$'\n'"FAIL guest machine: the guest didn't say aarch64 ($(head -c 200 "$OUT/guest-machine.txt" 2>/dev/null))"
grep -q '^MISSING ' "$OUT/guest-tools.txt" 2>/dev/null && lines+=$'\n'"FAIL harness-tools: $(grep '^MISSING ' "$OUT/guest-tools.txt" | tr '\n' ' ')(guest-tools.txt has apt's log)"
lines+=$'\n'"LIMIT Birth times: ext4 keeps them, Linux can't set them (restic BACKOTTER.md unix.birthtime: not on Linux)"
lines+=$'\n'"LIMIT SELinux labels: not applicable; the guest runs AppArmor, which keeps no labels on disk"
lines+=$'\n'"LIMIT NFSv4 ACLs: Linux has them only over NFS (system.nfs4_acl); not on a local file system"
[[ -n $FILTER ]] && lines=$(grep -E -- "$FILTER" <<<"$lines")
echo "$lines"
printf '%s\n' "$lines" >"$OUT/lines.txt"
python3 -c '
import json, sys
rows = []
for l in open(sys.argv[1]).read().splitlines():
    v, rest = l.split(" ", 1); need, _, det = rest.partition(": ")
    rows.append({"verdict": v, "need": need, "detail": det})
json.dump({"guest": sys.argv[4], "bin": sys.argv[3], "results": rows}, open(sys.argv[2], "w"), indent=1)' "$OUT/lines.txt" "$OUT/results.json" "$BIN" "$G"
cp "$OUT/results.json" "$STC_LOGS/results.json" 2>/dev/null
echo "logs: $OUT"
! grep -q '^FAIL ' <<<"$lines"
