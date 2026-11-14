#!/usr/bin/env bash
# SticTest1's targeted check for microos (openSUSE MicroOS: / a read-only
# btrfs snapper slot, @/.snapshots/N/snapshot with the ro property, the
# default subvolume; /etc a nested writable subvolume per slot, bind-mounted
# from fstab; /var and the rest writable @/ subvolumes; the ESP at
# /boot/efi (vfat, the fallback shim only); SELinux enforcing, glibc,
# systemd, transactional-update; docs/design/aeon.md).  From tumbleweed's
# check, with chimera-uefi's FAT fixture for the ESP.  Boots a throwaway
# overlay (vm-run --snapshot) with a spare disk; in the guest (inner.sh,
# run from /var/lib/bko-stic, since / is read-only) builds the fixture
# (fixture.sh: the transactional slots and state as the guest has them) on
# a fresh btrfs made like the root's, and fixture-vfat.sh (the guest's own
# ESP, FAT attributes) on a FAT made like the ESP, runs restic->restic,
# rustic->rustic, restic->rustic, rustic->restic, a restore over existing
# files with each tool, each tool's check of the other's repository,
# nonroot-inner.sh on btrfs (backup as root, restores as uid 4321), and
# judges (judge.py, fsmeta.py, fatattr.py, rt-common.sh's node comparison).
# The guest has no python3, attr, acl or libcap tools: the host's python3
# is shipped in with its glibc loader, libraries and standard library, and
# bkoshim.py stands in for setfattr, setfacl and setcap.
#
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR: restic-bko and rustic-bko for Linux (roundN/bin/linux)
# Prints "PASS|FAIL|LIMIT need: detail", writes results.json beside the
# logs (~/backotter-work/stictest1/microos/logs/PREFIX-*), exits 0 if
# nothing FAILs.  Takes a guest slot and a cmp-run.sh reservation itself.
set -u
here=$(cd "$(dirname "$0")" && pwd)
WORK=$HOME/backotter-work
[[ $# -ge 3 ]] || { echo "usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PREFIX=$3 FILTER=${4:-}
if [[ -z ${BKST_INNER:-} ]]; then
    # shellcheck source=/dev/null
    . "${BKO_TESTVM:-$WORK/testvm}/compare/guest-slot.sh"
    slot_take 3 "$(slot_range_here)"
    BKST_INNER=1 HOT_C=90 "$WORK/cmp-run.sh" --reserve 3 6 "$here/check.sh" "$@"
    rc=$?
    slot_give
    exit $rc
fi
STC_LOGS=${STC_LOGS:-$WORK/stictest1/microos/logs}
S=/mnt/bko-ram/$PREFIX/microos LOGS=$STC_LOGS/$PREFIX-$(date +%Y%m%d-%H%M%S)
mkdir -p "$S" "$LOGS"
export TMPDIR=$S
. "${BKO_TESTVM:-$WORK/testvm}/vm-lib.sh"
cleanup() { vm_halt; rm -rf "$S"; rmdir "/mnt/bko-ram/$PREFIX" 2>/dev/null; }
trap cleanup EXIT
qemu-img create -q -f qcow2 "$S/spare.qcow2" 16G
VM_BOOT_TIMEOUT=300 vm_boot microos "$PORT" "$S/vm" --snapshot --memory 4096 --cpus 2 --disk "$S/spare.qcow2" ||
    { echo "FAIL harness: microos didn't boot"; exit 2; }
T=60 vm_ssh "mkdir -p -m 755 /var/lib/bko-stic"
# The guest has no python3 (fsmeta.py, the shims, the judge need one): the
# host's, shipped in with its glibc loader and the libraries it and its
# extension modules link, its standard library beside it (py/bin, py/lib:
# Python finds its prefix from where it runs), started by
# /usr/local/bin/python3 (inner.sh).
PYB=$(python3 -c 'import os, sys; print(os.path.realpath(sys.executable))')
PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
PS=$S/ship/py
mkdir -p "$PS/bin" "$PS/so" "$PS/lib"
cp -L "$PYB" "$PS/bin/python3.real"
tar -C /usr/lib -cf - --exclude=__pycache__ --exclude=test --exclude=tests --exclude=idlelib --exclude=tkinter \
    --exclude=turtledemo --exclude=ensurepip --exclude=site-packages --exclude=dist-packages --exclude='_test*.so' \
    "python$PYV" | tar -C "$PS/lib" -xf -
{ ldd "$PYB"; for so in "$PS/lib/python$PYV/lib-dynload/"*.so; do ldd "$so"; done; } 2>/dev/null |
    awk '$2 == "=>" && $3 ~ /^\// { print $3 }' | sort -u | while read -r so; do cp -L "$so" "$PS/so/"; done
cp -L /lib64/ld-linux-x86-64.so.2 "$PS/so/"
tar -C "$S/ship" -czf "$S/py.tgz" py && rm -rf "$S/ship" || { echo "FAIL harness: no python3 to ship ($PYB)"; exit 2; }
vm_put "$S/py.tgz" /var/lib/bko-stic/py.tgz 644 && T=120 vm_ssh "tar -C /var/lib/bko-stic -xzf /var/lib/bko-stic/py.tgz && rm /var/lib/bko-stic/py.tgz" ||
    { echo "FAIL harness: shipping python3"; exit 2; }
for f in "$BIN/restic-bko" "$BIN/rustic-bko" "${BKO_TESTVM:-$WORK/testvm}/compare/fsmeta.py" "${BKO_TESTVM:-$WORK/testvm}/compare/rt-common.sh" \
         "${BKO_TESTVM:-$WORK/testvm}/compare/nonroot-inner.sh" "$here/fixture.sh" "$here/fixture-vfat.sh" "$here/inner.sh" "$here/judge.py" \
         "$here/bkoshim.py" "$here/fatattr.py"; do
    vm_put "$f" "/var/lib/bko-stic/$(basename "$f")" || { echo "FAIL harness: copying $f"; exit 2; }
done
T=900 vm_ssh "bash /var/lib/bko-stic/inner.sh" >"$LOGS/check.out" 2>&1
echo "inner exit $?" >>"$LOGS/check.out"
T=300 vm_ssh "cat /var/lib/bko-stic/logs.tar" 2>"$LOGS/tar.err" | tar xf - -C "$LOGS" 2>>"$LOGS/tar.err"
# The non-root run, judged as run-all.sh judges nonroot-rt.sh's log.
nr=$(python3 "${BKO_TESTVM:-$WORK/testvm}/compare/verdict.py" judge nonroot "$LOGS/nonroot.log" --full "$LOGS/nonroot.log" 2>&1)
echo "$nr" >"$LOGS/nonroot.verdict"
python3 - "$LOGS" <<'PY' >>"$LOGS/check.out"
import json, sys, os
L = sys.argv[1]
rows = []
for l in open(os.path.join(L, "nonroot.verdict")):
    try: rows.append(json.loads(l))
    except Exception: pass
bad = [r for r in rows if str(r.get("verdict", r.get("result", ""))).upper() not in ("PASS",)]
if not rows: print("FAIL [btrfs] non-root backup and restore (uid 4321): no verdict: " + open(os.path.join(L, "nonroot.verdict")).read()[:400])
elif bad: print("FAIL [btrfs] non-root backup and restore (uid 4321): " + json.dumps(bad)[:900])
else: print(f"PASS [btrfs] non-root backup and restore (uid 4321): {len(rows)} rows pass (verdict.py judge nonroot)")
PY
grep -E '^(PASS|FAIL|LIMIT) ' "$LOGS/check.out" | { [[ -n $FILTER ]] && grep -E -- "$FILTER" || cat; } | tee "$LOGS/lines.txt"
python3 - "$LOGS" >"$LOGS/results.json" <<'PY'
import json, sys, os, re
L = sys.argv[1]
out = []
for l in open(os.path.join(L, "lines.txt")):
    m = re.match(r"(PASS|FAIL|LIMIT) (.*?): (.*)", l.rstrip("\n"))
    if m: out.append({"result": m[1], "need": m[2], "detail": m[3]})
json.dump({"guest": "microos", "logs": L, "results": out}, sys.stdout, indent=1)
PY
cp "$LOGS/results.json" "$STC_LOGS/results.json"
[[ -s $LOGS/lines.txt ]] || { echo "FAIL harness: no results (see $LOGS/check.out)"; exit 1; }
! grep -q '^FAIL ' "$LOGS/lines.txt"
