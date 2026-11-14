#!/usr/bin/env bash
# SticTest1's targeted check for solus49 (Solus 4.9: glibc, systemd,
# eopkg; UEFI with Secure Boot: shim (EFI/BOOT/BOOTX64.EFI and
# EFI/com.solus-project/bootloaderx64.efi) -> systemd-boot (grubx64.efi) ->
# the kernel, which clr-boot-manager copies to the ESP from /usr/lib64/kernel;
# Solus's MOK enrolled.  GPT: a 2 GiB ESP (GPT name "EFI") that clr-boot-manager
# mounts at /boot on demand (not in fstab), an 8 MiB BIOS boot partition, ext4
# / (defaults,noatime); no swap partition (zram); docs/design/solus.md).
# kali's check (an ext4 like /, ext4's optional features on their own ext4,
# the ESP with chimera-uefi's fatattr.py) with aerynos's ESP that holds the
# kernels: a scratch disk laid out from the guest's own `sfdisk -d`, file
# systems mounted inside the backed-up path, each restore target a disk image
# laid out the same.
#
# Boots a throwaway overlay (vm-run --snapshot; the guest's vm-run-options:
# --uefi --mok ..., Secure Boot on) with two spare disks: vdb laid out like
# the guest's vda (its GPT: each partition's start, size, type and name: the
# ESP, BIOS boot, ext4 /), vdc for the repositories, the restore targets and
# the logs.  The fixture (inner.sh) is on the ext4 made like / (the guest's
# features, inode and block size, mounted with fstab's options), with the
# ESP (FAT32 made in the guest with its ESP's geometry, mounted with the
# options clr-boot-manager mounts it with) at tree/boot and an ext4 with
# project quotas and casefolding at tree/feat, the guest's own files as real
# examples (set-id programs, shadow, fstab, /usr/lib -> lib64,
# /usr/lib64/kernel's current kernel, initrd, cmdline, config and symlinks,
# /etc/kernel/cmdline.d and cmdline-removal.d, eopkg's /var/lib/eopkg,
# tester's home, the journal if kept, the ESP's shim, systemd-boot,
# MokManager, loader.conf, the entries, the current kernel and initrd and
# solus-enroll-me.cer with their FAT attributes).  Runs restic->restic,
# rustic->rustic, restic->rustic, rustic->restic, a restore over existing
# immutable/append-only files and changed FAT attributes with each tool, each
# tool's check --read-data of the other's repository, one non-root run (uid
# 4321's own tree), and judges (judge.py, fsmeta.py, fatattr.py,
# rt-common.sh's node comparison).  Never a whole-disk run: the GPT, the BIOS
# boot partition, shim's NVRAM entry, Secure Boot and the MOK are HoltTest1's
# (a LIMIT here).  Solus has attr, acl, e2fsprogs, dosfstools and python3
# (the build's package list): the FAT32s are made in the guest (mkfs.vfat),
# and the fixture still sets its xattrs and ACLs from Python, as kali's does.
#
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR: restic-bko and rustic-bko for Linux (roundN/bin/linux)
# Prints "PASS|FAIL|LIMIT need: detail", writes results.json beside the
# logs (~/backotter-work/stictest1/solus49/logs/PREFIX-*), exits 0 if nothing
# FAILs.  Takes a guest slot and a cmp-run.sh reservation itself.
set -u
GUEST=solus49
here=$(cd "$(dirname "$0")" && pwd)
WORK=$HOME/backotter-work
[[ $# -ge 3 ]] || { echo "usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PREFIX=$3 FILTER=${4:-}
# cleanup removes the scratch from a RAM disk other sessions share: it has to
# stay inside /mnt/bko-ram/PREFIX.
[[ $PREFIX =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "check.sh: PREFIX must be a plain name" >&2; exit 2; }
for b in restic-bko rustic-bko; do [[ -x $BIN/$b ]] || { echo "FAIL binaries: no $BIN/$b"; exit 1; }; done
if [[ -z ${BKST_INNER:-} ]]; then
    # shellcheck source=/dev/null
    . "${BKO_TESTVM:-$WORK/testvm}/compare/guest-slot.sh"
    slot_take 3 "$(slot_range_here)"
    # 4 GiB: the fixture holds one kernel and initrd twice (the ESP's and
    # /usr/lib64/kernel's) and eopkg's DB, in the source and 4 targets; S4's
    # runs used 3.0 GiB (ram-used: 3010 MiB on Cinqcent, 3001 on Chopin).
    BKST_INNER=1 HOT_C=90 "$WORK/cmp-run.sh" --reserve 4 5 "$here/check.sh" "$@"
    rc=$?
    slot_give
    exit $rc
fi
STC_LOGS=${STC_LOGS:-$WORK/stictest1/$GUEST/logs}
S=/mnt/bko-ram/$PREFIX/$GUEST LOGS=$STC_LOGS/$PREFIX-$(date +%Y%m%d-%H%M%S)
mkdir -p "$S" "$LOGS"
export TMPDIR=$S
# shellcheck source=/dev/null
. "${BKO_TESTVM:-$WORK/testvm}/vm-lib.sh"
ram_used() {  # MiB this run holds in the RAM disk: its files and QEMU's --snapshot overlays (deleted, open)
    local p q k=0 f
    desc() { local c; for c in $(pgrep -P "$1"); do echo "$c"; desc "$c"; done; }
    [[ -n ${VM_PID:-} ]] && for p in $VM_PID $(desc "$VM_PID"); do
        [[ $(cat /proc/$p/comm 2>/dev/null) == qemu* ]] || continue
        for f in /proc/$p/fd/*; do
            [[ $(readlink "$f") == "$S"/*" (deleted)" ]] && q=$(stat -L -c '%b*%B' "$f" 2>/dev/null) && k=$((k + q))
        done
    done
    echo "$(( ($(du -sk "$S" | cut -f1) * 1024 + k) >> 20 )) MiB in $S (overlays $((k >> 20)) MiB)"
}
cleanup() {  # the RAM the run took (for --reserve), then everything
    ram_used >"$LOGS/ram-used" 2>&1; vm_halt; rm -rf "$S"; rmdir "/mnt/bko-ram/$PREFIX" 2>/dev/null
}
trap cleanup EXIT
# vdb: the guest-like disk, the guest's vda's size (20 GiB); vdc: work.
qemu-img create -q -f qcow2 "$S/stack.qcow2" 20G && qemu-img create -q -f qcow2 "$S/work.qcow2" 80G
VM_BOOT_TIMEOUT=300 vm_boot $GUEST "$PORT" "$S/vm" --snapshot --memory 3072 --cpus 2 \
    --disk "$S/stack.qcow2" --disk "$S/work.qcow2" ||
    { echo "FAIL harness: $GUEST didn't boot"; exit 2; }
T=60 vm_ssh "mkdir -p /root/bk"
for f in "$BIN/restic-bko" "$BIN/rustic-bko" "${BKO_TESTVM:-$WORK/testvm}/compare/fsmeta.py" "${BKO_TESTVM:-$WORK/testvm}/compare/rt-common.sh" \
         "${BKO_TESTVM:-$WORK/testvm}/compare/torture.sh" "$here/inner.sh" "$here/judge.py" "$here/fatattr.py" "$here/nracl.py"; do
    vm_put "$f" "/root/bk/$(basename "$f")" || { echo "FAIL harness: copying $f"; exit 2; }
done
T=1800 vm_ssh "bash /root/bk/inner.sh" >"$LOGS/check.out" 2>&1
echo "inner exit $?" >>"$LOGS/check.out"
# inner.sh's own tar, else (a timeout) what its logs dir holds so far, with the processes then
T=60 vm_ssh "ps -eo pid,ppid,etime,time,stat,rss,args --forest; free -m; df -m" >"$LOGS/guest-ps.txt" 2>&1
T=300 vm_ssh "if [ -f /root/bk/logs.tar ]; then cat /root/bk/logs.tar; else tar -C /mnt/bkw/logs -cf - .; fi" 2>"$LOGS/tar.err" | tar xf - -C "$LOGS" 2>>"$LOGS/tar.err"
cp "$S/vm/serial.log" "$LOGS/serial.log" 2>/dev/null
grep -E '^(PASS|FAIL|LIMIT) ' "$LOGS/check.out" | { [[ -n $FILTER ]] && grep -E -- "$FILTER" || cat; } | tee "$LOGS/lines.txt"
python3 - "$LOGS" "$GUEST" "$BIN" >"$LOGS/results.json" <<'PY'
import json, sys, os, re
L = sys.argv[1]
out = []
for l in open(os.path.join(L, "lines.txt")):
    m = re.match(r"(PASS|FAIL|LIMIT) (.*?): (.*)", l.rstrip("\n"))
    if m: out.append({"result": m[1], "need": m[2], "detail": m[3]})
json.dump({"guest": sys.argv[2], "bin": sys.argv[3], "logs": L, "results": out}, sys.stdout, indent=1)
PY
cp "$LOGS/results.json" "$STC_LOGS/results.json"
echo "logs: $LOGS"
[[ -s $LOGS/lines.txt ]] || { echo "FAIL harness: no results (see $LOGS/check.out)"; exit 1; }
! grep -q '^FAIL ' "$LOGS/lines.txt"
